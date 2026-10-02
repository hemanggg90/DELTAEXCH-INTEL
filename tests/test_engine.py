"""P5: monitor exits, account tracker, recovery, planner, engine cycle, WebSocket feed, chain recorder."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.brokers.paper_broker import PaperBroker, TradePlan
from delta_intelligence.brokers.ws_feed import TickerFeed, parse_message
from delta_intelligence.config.settings import Settings
from delta_intelligence.config.watchlist import UNDERLYINGS
from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot, Decision, Position
from delta_intelligence.execution import chain_recorder
from delta_intelligence.execution.account import AccountTracker
from delta_intelligence.execution.engine import TradingEngine, kill_switch_on, set_kill_switch
from delta_intelligence.execution.monitor import decide_exits
from delta_intelligence.execution.planner import plan_trade
from delta_intelligence.execution.recovery import recover
from delta_intelligence.options.chain import OptionChain, OptionQuote
from delta_intelligence.options.structures import long_call
from delta_intelligence.strategies.base import Setup
from delta_intelligence.strategies.active import ACTIVE_VARIANTS

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
EXP = pd.Timestamp("2026-10-03T12:00Z")


def q(sym, kind, strike, bid, ask, delta, expiry=EXP, iv=0.45, oi=500.0, size=50.0, spot=85000.0):
    return OptionQuote(sym, "BTC", kind, float(strike), expiry, bid, ask, (bid + ask) / 2 if bid and ask else None, iv,
                       oi, size, size, delta, spot, 1, 0.001, None, 10.0, 0.1)


def chain_at(spot=85000.0, bump=0.0):
    rows = []
    for k, dc in ((84800, 0.58), (85000, 0.52), (85200, 0.45)):
        c_mid = 700 + (spot - k) * 0.5 + bump
        p_mid = 700 - (spot - k) * 0.5 - bump
        rows.append(q(f"C-BTC-{k}-031026", "C", k, c_mid - 5, c_mid + 5, dc, spot=spot))
        rows.append(q(f"P-BTC-{k}-031026", "P", k, p_mid - 5, p_mid + 5, dc - 1, spot=spot))
    return OptionChain(rows)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHLIST", "BTCUSD")
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'e.db').as_posix()}")
    s = Settings.from_env({"PAPER_STARTING_CAPITAL_USD": "10000", "WATCHLIST": "BTCUSD"})
    box = {"chain": chain_at()}
    broker = PaperBroker(lambda: box["chain"], s, clock=lambda: NOW)
    yield s, box, broker
    db.reset_engine()


def open_call(broker, stop=84000.0, target=87000.0, time_stop=None, prem_stop=35.0):
    st = long_call("BTC", 85000, EXP.to_pydatetime(), 10, 0.001, "C-BTC-85000-031026")
    return broker.open_structure(TradePlan("S", st, stop, target, time_stop_at=time_stop, premium_stop_pct=prem_stop))


# ---- monitor -------------------------------------------------------------------------------------------------------
def test_monitor_exit_reasons(env) -> None:
    s, box, broker = env
    r = open_call(broker, time_stop=NOW + dt.timedelta(hours=4))
    items = broker.open_positions()
    assert decide_exits(items, box["chain"], {"BTC": 85000}, NOW, 2.0) == []
    assert decide_exits(items, box["chain"], {"BTC": 83900}, NOW, 2.0)[0].reason == "UNDERLYING_STOP"
    assert decide_exits(items, box["chain"], {"BTC": 87100}, NOW, 2.0)[0].reason == "TARGET"
    assert decide_exits(items, chain_at(bump=-400), {"BTC": 85000}, NOW, 2.0)[0].reason == "PREMIUM_STOP"
    assert decide_exits(items, box["chain"], {"BTC": 85000}, NOW + dt.timedelta(hours=5), 2.0)[0].reason == "TIME_STOP"
    guard = EXP.to_pydatetime() - dt.timedelta(hours=1)
    assert decide_exits(items, box["chain"], {"BTC": 85000}, guard, 2.0)[0].reason == "EXPIRY_GUARD"
    assert decide_exits(items, box["chain"], {}, EXP.to_pydatetime() + dt.timedelta(minutes=1), 2.0)[0].action == "SETTLE"
    assert r.ok


# ---- account tracker -----------------------------------------------------------------------------------------------
def test_risk_day_rolls_at_midnight_ist(env) -> None:
    s, box, broker = env
    tr = AccountTracker(s)
    before = dt.datetime(2026, 10, 2, 18, 20, tzinfo=UTC)  # 23:50 IST
    tr.update({"equity": 10_000}, before)
    st = tr.update({"equity": 9_800}, before + dt.timedelta(minutes=5))
    assert st["day_start_equity"] == 10_000 and st["peak_equity"] == 10_000
    st = tr.update({"equity": 9_800}, dt.datetime(2026, 10, 2, 18, 31, tzinfo=UTC))  # 00:01 IST next day
    assert st["day_start_equity"] == 9_800
    open_call(broker)
    assert tr.trades_today(NOW) == 1


# ---- planner ---------------------------------------------------------------------------------------------------
def test_planner_picks_delta_band_sizes_and_proposes(env) -> None:
    s, box, broker = env
    v = ACTIVE_VARIANTS[0]
    strat = v.strategy()
    setup = Setup(pd.Timestamp(NOW) - pd.Timedelta("5min"), "LONG", 85000, 84600, 85900)
    res = plan_trade(strat, v.moneyness, setup, UNDERLYINGS["BTCUSD"], box["chain"], 85000, 85000, NOW, s,
                     broker.account_snapshot(), relative_volume=1.0, iv_percentile=40)
    assert res.ok, res.reason
    leg = res.plan.structure.legs[0]
    assert leg.kind == "C" and leg.strike == 85000 and leg.side == 1
    assert res.proposed.premium_at_risk <= 10_000 * 0.005 and res.details["contracts"] >= 1
    itm = plan_trade(ACTIVE_VARIANTS[1].strategy(), "ITM1", setup, UNDERLYINGS["BTCUSD"], box["chain"], 85000, 85000,
                     NOW, s, broker.account_snapshot())
    assert itm.ok and itm.plan.structure.legs[0].strike == 84800  # 1-ITM call (delta 0.58 in band)


def test_planner_no_expiry_far_enough(env) -> None:
    s, box, broker = env
    strat = ACTIVE_VARIANTS[0].strategy()
    strat.expected_hold_bars = 12 * 30  # 30 h hold needs >= 75 h to expiry
    res = plan_trade(strat, "ATM", Setup(pd.Timestamp(NOW), "LONG", 85000, 84600, 85900), UNDERLYINGS["BTCUSD"],
                     box["chain"], 85000, 85000, NOW, s, broker.account_snapshot())
    assert not res.ok and "expiry" in res.reason


# ---- recovery ------------------------------------------------------------------------------------------------------
class FakeDM:
    def __init__(self, low=84500.0, high=85500.0):
        self.low, self.high = low, high

    def get_ohlcv(self, symbol, tf, start, end=None, **kw):
        ts = pd.date_range(pd.Timestamp(start).floor("5min"), pd.Timestamp(end or start), freq="5min")
        n = len(ts)
        close = np.full(n, 85000.0)
        return pd.DataFrame({"timestamp": ts, "open": close, "high": np.full(n, self.high), "low": np.full(n, self.low),
                             "close": close, "volume": 1.0}), {"quality_status": "OK"}


def test_recovery_settles_expired_and_closes_missed_stops(env) -> None:
    s, box, broker = env
    r1 = open_call(broker)
    db.set_state("engine_heartbeat", (NOW - dt.timedelta(hours=1)).isoformat())
    later = NOW + dt.timedelta(hours=2)
    summary = recover(broker, FakeDM(low=83000), later)  # perp traded below the 84000 stop while down
    assert summary["closed"] == [r1.position_id]
    r2 = open_call(broker)
    summary = recover(broker, FakeDM(), EXP.to_pydatetime() + dt.timedelta(hours=1))
    assert summary["settled"] == [r2.position_id]
    with db.get_session() as ss:
        assert {p.status for p in ss.query(Position).all()} == {"CLOSED", "SETTLED"}


# ---- engine ----------------------------------------------------------------------------------------------------
def flip_frame(direction=1):
    ts = pd.date_range(pd.Timestamp(NOW) - pd.Timedelta(hours=2), periods=24, freq="5min")
    sd = np.full(24, -direction, dtype=float)
    sd[-1] = direction
    return pd.DataFrame({"timestamp": ts, "open": 85000.0, "high": 85010.0, "low": 84990.0, "close": 85000.0,
                         "volume": 1.0, "supertrend": 84600.0 if direction == 1 else 85400.0,
                         "supertrend_direction": sd, "atr_14": 800.0, "relative_volume": 1.0, "index_close": 85000.0})


def make_engine(env, frame_fn):
    s, box, broker = env
    return TradingEngine(broker, lambda: box["chain"], FakeDM(), s, clock=lambda: NOW, interval_sec=0.05,
                         frame_builder=frame_fn)


def test_breakeven_gate_can_veto_atm_but_pass_itm(env) -> None:
    def small_target(perp, now):
        f = flip_frame()
        f["atr_14"] = 400.0  # target 900 away: ATM extrinsic ~700 x 1.25 fails, 1-ITM extrinsic ~600 passes
        return f, "OK"

    eng = make_engine(env, small_target)
    eng.run_cycle()
    with db.get_session() as s:
        assert [p.strategy for p in s.query(Position).all()] == ["Supertrend tight / 1-ITM"]
        assert "Breakeven gate" in s.query(Decision).one().ranking["detail"]


def test_engine_opens_both_variants_on_a_flip(env) -> None:
    eng = make_engine(env, lambda perp, now: (flip_frame(), "OK"))
    eng.run_cycle()
    with db.get_session() as s:
        pos = s.query(Position).all()
        assert sorted(p.strategy for p in pos) == sorted(v.key for v in ACTIVE_VARIANTS)
        assert s.query(Decision).one().setup_status == "TRIGGERED"
    eng.run_cycle()  # same bar: no second scan
    with db.get_session() as s:
        assert s.query(Position).count() == 2


def test_kill_switch_blocks_new_trades(env) -> None:
    set_kill_switch(True, "test")
    assert kill_switch_on()
    eng = make_engine(env, lambda perp, now: (flip_frame(), "OK"))
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0
        assert "kill switch" in s.query(Decision).one().no_trade_reason


def test_failing_cycle_does_not_kill_thread_and_stop_is_idempotent(env) -> None:
    calls = {"n": 0}

    def boom(perp, now):
        calls["n"] += 1
        raise RuntimeError("data down")

    eng = make_engine(env, boom)
    eng.chain_fn = lambda: (_ for _ in ()).throw(ConnectionError("rest down"))
    eng.start()
    eng.start()  # second start is a no-op
    time.sleep(0.3)
    assert eng.is_running() and eng.cycle_errors >= 2
    eng.stop()
    eng.stop()
    time.sleep(0.2)
    assert not eng.is_running()


# ---- websocket feed -------------------------------------------------------------------------------------------------
def test_parse_compact_and_full_ticker() -> None:
    compact = {"type": "ticker", "sy": "BTCUSD", "sp": "86377.9", "ts": 1,
               "d": [{"s": "BTCUSD", "m": "86343.6", "q": ["86343.5", "2134", "86343", "3273", None],
                      "qiv": [None, None, "0.4"]}]}
    lq = parse_message(compact, 5.0)[0]
    assert (lq.symbol, lq.ask, lq.bid, lq.mark, lq.spot) == ("BTCUSD", 86343.5, 86343.0, 86343.6, 86377.9)
    full = {"type": "v2/ticker", "symbol": "BTCUSD", "mark_price": "1", "spot_price": "2",
            "quotes": {"best_bid": "0.9", "best_ask": "1.1"}}
    assert parse_message(full, 1.0)[0].bid == 0.9
    assert parse_message({"type": "heartbeat"}, 1.0) == []


class FakeWS:
    def __init__(self, script):
        self.script, self.sent = list(script), []

    def settimeout(self, t):
        pass

    def send(self, m):
        self.sent.append(json.loads(m))

    def recv(self):
        if not self.script:
            time.sleep(0.01)
            raise ConnectionError("dropped")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    def close(self):
        pass


def test_feed_subscribes_reads_and_reconnects() -> None:
    conns = []
    tick = {"type": "ticker", "sp": "1", "d": [{"s": "BTCUSD", "m": "100", "q": ["101", "1", "99", "1", None]}]}

    def connect(url):
        ws = FakeWS([{"type": "subscriptions"}, tick])
        conns.append(ws)
        return ws

    feed = TickerFeed("wss://x", ["BTCUSD"], connect=connect)
    import delta_intelligence.brokers.ws_feed as wf
    feed._stop.wait = lambda t: threading.Event().wait(0.01)  # fast backoff in tests
    feed.start()
    deadline = time.time() + 3
    while len(conns) < 2 and time.time() < deadline:
        time.sleep(0.02)
    feed.stop()
    assert len(conns) >= 2 and feed.reconnects >= 1  # dropped connection was re-established
    first = conns[0].sent
    assert first[0]["payload"]["channels"][0] == {"name": "ticker", "symbols": ["BTCUSD"]}
    assert {"type": "enable_heartbeat"} in first
    assert feed.quote("BTCUSD").mark == 100.0 and wf.MAX_CONNECTS_PER_5MIN <= 150


# ---- chain recorder ---------------------------------------------------------------------------------------------
def test_chain_recorder_writes_band_rows(env) -> None:
    s, box, broker = env
    far = q("C-BTC-99000-031026", "C", 99000, 1, 2, 0.01)
    chain = OptionChain(list(box["chain"].by_symbol.values()) + [far])
    n = chain_recorder.record(chain, {"BTC": 85000}, NOW)
    assert n == 6  # the 99000 strike is outside +-10%
    with db.get_session() as ss:
        assert ss.query(ChainSnapshot).count() == 6


def test_restart_does_not_rescan_the_same_bar_or_duplicate(env) -> None:
    eng = make_engine(env, lambda perp, now: (flip_frame(), "OK"))
    eng.run_cycle()
    fresh = make_engine(env, lambda perp, now: (flip_frame(), "OK"))  # a NEW process at the same bar
    fresh.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 2 and s.query(Decision).count() == 1


def test_one_position_per_variant_per_underlying(env) -> None:
    eng = make_engine(env, lambda perp, now: (flip_frame(), "OK"))
    eng.run_cycle()
    db.set_state("last_scanned_bar", {})
    eng.last_bar.clear()  # force a rescan of the same flip
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 2
        assert "already holding" in s.query(Decision).order_by(Decision.id.desc()).first().no_trade_reason


def test_second_engine_refuses_to_start_while_another_is_alive(env) -> None:
    from delta_intelligence.execution.engine import OWNER_KEY

    db.set_state(OWNER_KEY, {"id": "otherhost:999"})
    db.set_state("engine_heartbeat", NOW.isoformat())
    eng = make_engine(env, lambda perp, now: (flip_frame(), "OK"))
    with pytest.raises(RuntimeError, match="double-trade"):
        eng.start()
    db.set_state("engine_heartbeat", (NOW - dt.timedelta(minutes=10)).isoformat())  # stale owner: allowed
    eng.start()
    eng.stop()
