"""
Restart recovery: run once when the engine starts, BEFORE any new trade.

1. Logs an "engine restarted" event, with the downtime since the last heartbeat.
2. Settles positions whose expiry passed while the engine was down, at the settlement-index model (30-min TWAP of the
   5m index before expiry), using cached or refetched public index candles.
3. Closes positions whose underlying stop or target was crossed while down (from the perp candles since the last
   heartbeat). They exit at the CURRENT market, which may be worse than the level; the reason records that.

In LIVE mode the broker first reconciles the database against the exchange's positions and orders. A critical
mismatch engages the kill switch (no new trades) and is returned in the summary.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from delta_intelligence.config.watchlist import UNDERLYINGS
from delta_intelligence.database import db
from delta_intelligence.database.models import from_db_time
from delta_intelligence.utils.logging_utils import log_event

HEARTBEAT_KEY = "engine_heartbeat"


def last_heartbeat() -> dt.datetime | None:
    v = db.get_state(HEARTBEAT_KEY)
    return dt.datetime.fromisoformat(v) if v else None


def settlement_index(candles: pd.DataFrame, expiry: pd.Timestamp) -> float | None:
    win = candles[(candles["timestamp"] >= expiry - pd.Timedelta(minutes=30)) & (candles["timestamp"] < expiry)]
    return float(win["close"].mean()) if len(win) >= 3 else None


def recover(broker, data_manager, now: dt.datetime) -> dict:
    hb = last_heartbeat()
    down_min = (now - hb).total_seconds() / 60 if hb else None
    log_event("engine", "engine restarted", level="WARNING",
              downtime_minutes=None if down_min is None else round(down_min, 1),
              last_heartbeat=hb.isoformat() if hb else None)
    asset_to = {u.asset: u for u in UNDERLYINGS.values()}
    settled, closed, errors = [], [], []
    reconcile = None
    if getattr(broker, "mode", "PAPER") == "LIVE":
        try:
            reconcile = broker.reconcile()
        except Exception as exc:  # can't verify the exchange: do not trade blind
            from delta_intelligence.execution.engine import set_kill_switch

            reconcile = {"ok": False, "critical": [f"reconcile failed: {exc}"], "notes": []}
            set_kill_switch(True, f"live reconcile failed at start: {exc}")
    for it in broker.open_positions():
        p = it["position"]
        u = asset_to.get(p.underlying)
        expiry = pd.Timestamp(from_db_time(p.expiry))
        try:
            if now >= expiry:
                idx, _ = data_manager.get_ohlcv(u.index_symbol, "5m", (expiry - pd.Timedelta(hours=2)).to_pydatetime(),
                                                (expiry + pd.Timedelta(minutes=5)).to_pydatetime())
                s = settlement_index(idx, expiry)
                if s is None:
                    errors.append(f"{p.position_id}: no index candles to settle")
                    continue
                broker.settle_position(p.position_id, s)
                settled.append(p.position_id)
                continue
            since = hb or from_db_time(p.opened_at)
            # Nothing to look for without a stop, and no CLOSED 5m bar exists in a window under 5 minutes (Delta then
            # returns no candles, which used to be reported as an error). The engine's first cycle checks the live
            # perp price against the stop either way.
            if p.underlying_stop is None or p.direction not in ("LONG", "SHORT") or \
                    (now - since).total_seconds() < 300:
                continue
            perp, _ = data_manager.get_ohlcv(u.perp_symbol, "5m", since, now)
            if len(perp):
                hit = (perp["low"].min() <= p.underlying_stop) if p.direction == "LONG" else \
                    (perp["high"].max() >= p.underlying_stop)
                tgt = p.underlying_target is not None and (
                    (perp["high"].max() >= p.underlying_target) if p.direction == "LONG"
                    else (perp["low"].min() <= p.underlying_target))
                if hit or tgt:
                    reason = "MISSED_STOP_WHILE_DOWN" if hit else "MISSED_TARGET_WHILE_DOWN"
                    if broker.close_position(p.position_id, reason) is not None:
                        closed.append(p.position_id)
        except Exception as exc:
            errors.append(f"{p.position_id}: {type(exc).__name__}: {exc}")
    summary = {"downtime_minutes": down_min, "settled": settled, "closed": closed, "errors": errors,
               "reconcile": reconcile}
    log_event("engine", "restart recovery finished", level="WARNING" if errors else "INFO", **summary)
    return summary
