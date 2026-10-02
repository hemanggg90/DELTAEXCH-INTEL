"""
The 24x7 trading engine: one per process.

Streamlit holds it via `@st.cache_resource` (P6), so it survives reruns, page switches and closed tabs.
`scripts/run_engine.py` runs it headless. It owns daemon threads and NEVER calls `st.*` from them; the UI reads
`status()` and the database.

**Each cycle** (default every 30 s):
1. Kill switch from the DB; the live chain (one cached REST call); perp prices (WebSocket, with REST fallback).
2. **Exits first** for every open position (monitor.decide_exits): settle, expiry guard, underlying stop/target,
   premium stop, time stop.
3. For each underlying whose 5-minute bar has CLOSED since the last look:
   - refresh candles; compute features and the strategy context;
   - for each ACTIVE variant: setup on the latest closed bar → plan from the live chain → RISK ENGINE → paper broker;
   - store a Decision row.
4. Every `record_every` seconds: a chain snapshot (chain recorder).
5. Heartbeat to `engine_state`.

A failing cycle is logged and the loop continues. On start, restart recovery runs before any trading.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import traceback
from collections.abc import Callable

import pandas as pd

from delta_intelligence.config.settings import Settings, get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.database import db
from delta_intelligence.database.models import Decision, to_db_time
from delta_intelligence.execution import chain_recorder
from delta_intelligence.execution.account import AccountTracker
from delta_intelligence.execution.monitor import decide_exits
from delta_intelligence.execution.planner import plan_trade
from delta_intelligence.execution.recovery import HEARTBEAT_KEY, recover
from delta_intelligence.options.analytics import iv_percentile
from delta_intelligence.risk.risk_engine import evaluate_trade
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.utils.logging_utils import log_event, new_decision_id
from delta_intelligence.utils.timeutil import last_closed_bar_start, now_utc

KILL_SWITCH_KEY = "kill_switch"
LAST_BAR_KEY = "last_scanned_bar"  # persisted, so a restart never re-processes (and re-trades) the same bar
LOOKBACK_DAYS = 70  # features (EMA200, 24 h vol) + daily context (MA50) need this much history


def kill_switch_on() -> bool:
    v = db.get_state(KILL_SWITCH_KEY, {"on": False}) or {}
    return bool(v.get("on"))


def set_kill_switch(on: bool, reason: str = "") -> None:
    db.set_state(KILL_SWITCH_KEY, {"on": bool(on), "reason": reason, "at": now_utc().isoformat()})
    log_event("engine", f"kill switch {'ENGAGED' if on else 'released'}", level="WARNING", reason=reason)


class TradingEngine:
    def __init__(self, broker, chain_fn: Callable, data_manager, settings: Settings | None = None,
                 feed=None, iv_history: dict | None = None, clock: Callable[[], dt.datetime] = now_utc,
                 interval_sec: float = 30.0, record_every_sec: float = 300.0, variants=ACTIVE_VARIANTS,
                 frame_builder: Callable | None = None):
        self.broker = broker
        self.chain_fn = chain_fn
        self.dm = data_manager
        self.settings = settings or get_settings()
        self.feed = feed
        self.iv_history = iv_history or {}  # asset -> pd.Series of ATM IV indexed by availability time
        self.clock = clock
        self.interval = interval_sec
        self.record_every = record_every_sec
        self.variants = variants
        self.frame_builder = frame_builder or self._default_frame
        self.tracker = AccountTracker(self.settings)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at: dt.datetime | None = None
        self.last_cycle_at: dt.datetime | None = None
        self.last_error: str | None = None
        self.cycles = self.cycle_errors = 0
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.last_record: float = 0.0
        self.last_decisions: dict[str, dict] = {}
        self.recovery_summary: dict | None = None

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.started_at = self.clock()
            try:
                self.recovery_summary = recover(self.broker, self.dm, self.clock())
            except Exception as exc:
                log_event("engine", f"restart recovery failed: {exc}", level="ERROR")
                self.recovery_summary = {"error": str(exc)}
            if self.feed is not None:
                self.feed.start()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="trading-engine")
            self._thread.start()
            log_event("engine", "engine started", mode=self.broker.mode,
                      variants=[v.key for v in self.variants])

    def stop(self) -> None:
        self._stop.set()
        if self.feed is not None:
            self.feed.stop()
        log_event("engine", "engine stop requested")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        hb = db.get_state(HEARTBEAT_KEY)
        now = self.clock()
        return {"running": self.is_running(), "mode": self.broker.mode, "started_at": self.started_at,
                "uptime_sec": (now - self.started_at).total_seconds() if self.started_at and self.is_running() else 0,
                "last_heartbeat": hb, "last_cycle_at": self.last_cycle_at, "cycles": self.cycles,
                "cycle_errors": self.cycle_errors, "last_error": self.last_error,
                "kill_switch": kill_switch_on(), "ws_status": getattr(self.feed, "status", "off"),
                "last_decisions": dict(self.last_decisions), "recovery": self.recovery_summary,
                "variants": [v.key for v in self.variants]}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception as exc:
                self.cycle_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                log_event("engine", f"cycle failed: {self.last_error}", level="ERROR",
                          trace=traceback.format_exc()[-1500:])
            self._stop.wait(self.interval)

    # ---- one cycle ---------------------------------------------------------------------------------------------
    def _perp_prices(self, chain) -> dict[str, float]:
        out = {}
        for perp in get_watchlist():
            u = UNDERLYINGS[perp]
            q = self.feed.quote(perp, max_age=60) if self.feed is not None else None
            if q is not None and q.mark:
                out[u.asset] = q.mark
        return out

    def run_cycle(self) -> None:
        with self._lock:
            now = self.clock()
            chain = self.chain_fn()
            ks = kill_switch_on()
            perp_px = self._perp_prices(chain)
            missing = [UNDERLYINGS[p].asset for p in get_watchlist() if UNDERLYINGS[p].asset not in perp_px]
            if missing:  # REST fallback for perp prices
                for perp in get_watchlist():
                    u = UNDERLYINGS[perp]
                    if u.asset in missing:
                        try:
                            df, _ = self.dm.get_ohlcv(perp, "5m", now - dt.timedelta(hours=2), now)
                            if len(df):
                                perp_px[u.asset] = float(df["close"].iloc[-1])
                        except Exception:
                            pass
            # 1) exits first
            for ex in decide_exits(self.broker.open_positions(), chain, perp_px, now,
                                   self.settings.risk.expiry_guard_hours):
                self._execute_exit(ex, now)
            # 2) new bars -> strategies (last scanned bar is durable across restarts)
            persisted = db.get_state(LAST_BAR_KEY, {}) or {}
            for perp in get_watchlist():
                bar = pd.Timestamp(last_closed_bar_start(now, "5m"))
                seen = self.last_bar.get(perp) or (pd.Timestamp(persisted[perp]) if perp in persisted else None)
                if seen is not None and seen >= bar:
                    self.last_bar[perp] = seen
                    continue
                self.last_bar[perp] = bar
                persisted[perp] = bar.isoformat()
                db.set_state(LAST_BAR_KEY, persisted)
                self._scan(perp, chain, now, ks)
            # 3) chain recorder
            if time.monotonic() - self.last_record >= self.record_every:
                spots = {}
                for q in chain.by_symbol.values():
                    if q.spot:
                        spots.setdefault(q.underlying, q.spot)
                try:
                    n = chain_recorder.record(chain, spots, now)
                    self.last_record = time.monotonic()
                    log_event("chain_recorder", f"recorded {n} option rows", rows=n)
                except Exception as exc:
                    log_event("chain_recorder", f"snapshot failed: {exc}", level="WARNING")
            db.set_state(HEARTBEAT_KEY, now.isoformat())
            self.last_cycle_at = now
            self.cycles += 1

    def _execute_exit(self, ex, now) -> None:
        if ex.action == "SETTLE":
            from delta_intelligence.database.models import Position, from_db_time
            from delta_intelligence.execution.recovery import settlement_index

            with db.get_session() as s:
                p = s.query(Position).filter_by(position_id=ex.position_id).one()
                asset, expiry = p.underlying, pd.Timestamp(from_db_time(p.expiry))
            u = next(x for x in UNDERLYINGS.values() if x.asset == asset)
            idx, _ = self.dm.get_ohlcv(u.index_symbol, "5m", (expiry - pd.Timedelta(hours=2)).to_pydatetime(),
                                       (expiry + pd.Timedelta(minutes=5)).to_pydatetime())
            level = settlement_index(idx, expiry)
            if level is not None:
                self.broker.settle_position(ex.position_id, level)
            return
        self.broker.close_position(ex.position_id, ex.reason)

    def _default_frame(self, perp: str, now: dt.datetime):
        from delta_intelligence.features.feature_engine import compute_features
        from delta_intelligence.features.inputs import load_feature_inputs
        from delta_intelligence.strategies.context import build_strategy_frame

        inp = load_feature_inputs(self.dm, perp, "5m", now - dt.timedelta(days=LOOKBACK_DAYS), now)
        feats = compute_features(inp.ohlcv, "5m", inp.aux)
        return build_strategy_frame(inp.ohlcv, feats), inp.quality_status

    def _scan(self, perp: str, chain, now: dt.datetime, kill_switch: bool) -> None:
        u = UNDERLYINGS[perp]
        did = new_decision_id("SCAN")
        try:
            frame, quality = self.frame_builder(perp, now)
        except Exception as exc:
            self._record_decision(did, u.asset, now, "FAIL", None, f"data unavailable: {exc}", "NO_TRADE")
            return
        last = frame.iloc[-1]
        spot_q = next((q.spot for q in chain.by_symbol.values() if q.underlying == u.asset and q.spot), None)
        index_spot = spot_q or float(last.get("index_close") or last["close"])
        cur_iv = None
        exps = chain.expiries(u.asset)
        if exps:
            near = next((e for e in exps if (e - pd.Timestamp(now)).total_seconds() / 3600 >= 6), exps[-1])
            cur_iv = chain.atm_iv(u.asset, near, index_spot)
        ivp = iv_percentile(cur_iv, self.iv_history.get(u.asset, pd.Series(dtype=float)), pd.Timestamp(now)) \
            if cur_iv else None
        statuses = []
        held = {(it["position"].strategy, it["position"].underlying) for it in self.broker.open_positions()}
        for v in self.variants:
            strat = v.strategy()
            setup = strat.setup_now(frame)
            if setup is None:
                statuses.append(f"{v.key}: waiting for setup")
                continue
            if (v.key, u.asset) in held:
                statuses.append(f"{v.key}: setup {setup.direction} ignored (already holding a {u.asset} position)")
                continue
            snap = self.broker.account_snapshot()
            acct = self.tracker.account_state(snap, now, self.broker.is_connected(), kill_switch)
            res = plan_trade(strat, v.moneyness, setup, u, chain, index_spot, float(last["close"]), now,
                             self.settings, snap, relative_volume=last.get("relative_volume"), iv_percentile=ivp,
                             data_quality=quality)
            if not res.ok:
                statuses.append(f"{v.key}: setup {setup.direction}, not tradable ({res.reason})")
                continue
            decision = evaluate_trade(acct, res.proposed)
            if not decision.approved:
                statuses.append(f"{v.key}: VETO ({decision.reason})")
                continue
            res.plan.decision_id, res.plan.risk_decision_id = did, decision.decision_id
            opened = self.broker.open_structure(res.plan)
            statuses.append(f"{v.key}: {'OPENED ' + opened.position_id if opened.ok else 'fill failed: ' + opened.reason}")
        self._record_decision(did, u.asset, now, quality, ivp, "; ".join(statuses),
                              "TRIGGERED" if any("OPENED" in s for s in statuses) else "WAITING_FOR_SETUP")

    def _record_decision(self, did, asset, now, quality, ivp, text, status) -> None:
        self.last_decisions[asset] = {"at": now, "status": status, "detail": text, "iv_percentile": ivp}
        with db.get_session() as s:
            s.add(Decision(decision_id=did, underlying=asset, bar_time=to_db_time(pd.Timestamp(
                last_closed_bar_start(now, "5m"))), data_quality=quality, setup_status=status,
                no_trade_reason=text if status != "TRIGGERED" else None,
                selected_strategy=",".join(v.key for v in self.variants),
                ranking={"iv_percentile": ivp, "detail": text}))
