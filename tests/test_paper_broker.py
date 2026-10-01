from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from delta_intelligence.brokers.paper_broker import PaperBroker, TradePlan
from delta_intelligence.config.settings import Settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Order, Position, PositionLeg
from delta_intelligence.execution.sizing import per_contract_max_loss, size_contracts
from delta_intelligence.options.analytics import iv_percentile, summarize_chain
from delta_intelligence.options.chain import OptionChain, OptionQuote, expiry_from_symbol, parse_ticker
from delta_intelligence.options.structures import Leg, SellToOpenRejected, long_call, long_straddle

EXP = pd.Timestamp("2026-10-03T12:00Z")
NOW = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc)
S = 85000.0
C85, P85 = "C-BTC-85000-031026", "P-BTC-85000-031026"


def q(symbol, bid, ask, kind="C", strike=85000, oi=100.0, vol=10.0):
    return OptionQuote(symbol, "BTC", kind, float(strike), EXP, bid, ask, None if bid is None else (bid + ask) / 2,
                       0.45, oi, 10.0, 10.0, 0.5, S, 1, 0.001, None, vol, 0.1)


@pytest.fixture
def broker(tmp_path):
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    box = {"chain": OptionChain([q(C85, 495, 505), q(P85, 395, 405, "P")])}
    s = Settings.from_env({"PAPER_STARTING_CAPITAL_USD": "10000", "GST_RATE": "0.18", "SLIPPAGE_TICKS": "1"})
    b = PaperBroker(lambda: box["chain"], s, clock=lambda: NOW)
    b.box = box
    yield b
    db.reset_engine()


def test_buy_fills_at_ask_plus_slippage_and_moves_cash(broker) -> None:
    r = broker.open_structure(TradePlan("Momentum", long_call("BTC", 85000, EXP.to_pydatetime(), 100, 0.001, C85)))
    assert r.ok and r.fills[0][2] == pytest.approx(505.1)
    fee = min(0.0001 * S * 0.1, 0.035 * 505.1 * 0.1) * 1.18
    assert broker.cash == pytest.approx(10_000 - 50.51 - fee)
    with db.get_session() as s:
        o = s.query(Order).one()
        assert len(o.client_order_id) == 32 and (o.side, o.purpose, o.reduce_only) == ("buy", "OPEN", False)
        assert s.query(Position).one().max_loss == pytest.approx(50.51 + fee)


def test_sell_to_close_only_held_size(broker) -> None:
    r = broker.open_structure(TradePlan("M", long_call("BTC", 85000, EXP.to_pydatetime(), 10, 0.001, C85)))
    broker.box["chain"] = OptionChain([q(C85, 695, 705)])
    pnl = broker.close_position(r.position_id, "TARGET")
    with db.get_session() as s:
        close = s.query(Order).filter_by(purpose="CLOSE").one()
        assert (close.side, close.size, close.reduce_only) == ("sell", 10, True)
        pos = s.query(Position).one()
        assert pos.status == "CLOSED" and pos.realized_pnl == pytest.approx(pnl)
    assert pnl == pytest.approx((694.9 - 505.1) * 0.01 - pos.fees)
    assert broker.cash == pytest.approx(10_000 + pnl)


def test_broker_refuses_sell_to_open(broker) -> None:
    with db.get_session() as s:
        leg = Leg("C", 85000, EXP.to_pydatetime(), 1, 5, C85)
        with pytest.raises(SellToOpenRejected):
            broker._fill_leg(s, "POS-x", leg, False, 0.001, "OPEN", broker.chain())  # sell to open
        with pytest.raises(SellToOpenRejected):
            broker._fill_leg(s, "POS-x", leg, False, 0.001, "CLOSE", broker.chain(), held_long=2)  # beyond held


def test_straddle_partial_keeps_bought_leg(broker) -> None:
    broker.box["chain"] = OptionChain([q(C85, 495, 505), q(P85, None, None, "P")])
    r = broker.open_structure(TradePlan("Event", long_straddle("BTC", 85000, EXP.to_pydatetime(), 10, 0.001, (C85, P85))))
    assert r.ok and r.incomplete
    with db.get_session() as s:
        assert s.query(Position).one().structure == "LONG_CALL"
        assert {lg.status for lg in s.query(PositionLeg).all()} == {"OPEN", "FAILED"}


def test_unfillable_aborts_and_settlement(broker) -> None:
    broker.box["chain"] = OptionChain([q(C85, None, None)])
    assert not broker.open_structure(TradePlan("M", long_call("BTC", 85000, EXP.to_pydatetime(), 1, 0.001, C85))).ok
    broker.box["chain"] = OptionChain([q(C85, 495, 505)])
    r = broker.open_structure(TradePlan("M", long_call("BTC", 85000, EXP.to_pydatetime(), 10, 0.001, C85)))
    pnl = broker.settle_position(r.position_id, 86000.0)
    with db.get_session() as s:
        pos = s.query(Position).filter_by(position_id=r.position_id).one()
    assert pos.status == "SETTLED" and pos.exit_value == pytest.approx(10.0)
    assert pnl == pytest.approx(10.0 - pos.entry_net_premium - pos.fees)


def test_book_survives_restart_and_bucket_exposure(broker) -> None:
    r = broker.open_structure(TradePlan("M", long_call("BTC", 85000, EXP.to_pydatetime(), 10, 0.001, C85)))
    fresh = PaperBroker(broker.chain, broker.settings, clock=broker.clock)
    assert fresh.cash == pytest.approx(broker.cash)
    assert [it["position"].position_id for it in fresh.open_positions()] == [r.position_id]
    snap = fresh.account_snapshot()
    assert snap["exposure_by_bucket"]["CRYPTO_MAJORS"] == pytest.approx(snap["total_exposure"])
    assert snap["total_exposure"] > 0


def test_sizing() -> None:
    st = long_call("BTC", 85000, EXP.to_pydatetime(), 1, 0.001)
    per = per_contract_max_loss(st, [505.0], S, 0.0001, 0.035, 0.18)
    assert per == pytest.approx(0.505 + 2 * min(0.0085, 0.035 * 0.505) * 1.18)
    assert size_contracts(10_000, 0.5, per, 10_000, per) == int(0.9 * 50 / per)
    assert size_contracts(100, 0.5, per, 100, per) == 0


def test_chain_parsing_and_analytics() -> None:
    t = {"symbol": "C-BTC-87000-021026", "strike_price": "87000", "mark_price": "26.3", "product_id": 5,
         "contract_value": "0.001", "oi_contracts": "469", "volume": "120", "tick_size": "0.1",
         "quotes": {"best_bid": "10.6", "best_ask": "12", "mark_iv": "0.35", "bid_size": "1000", "ask_size": "6037"},
         "greeks": {"delta": "0.048", "spot": "84764.4"}}
    oq = parse_ticker(t)
    assert oq.expiry == pd.Timestamp("2026-10-02T12:00Z") and oq.mid == pytest.approx(11.3) and oq.volume == 120
    chain = OptionChain([q(C85, 495, 505, oi=300, vol=50), q(P85, 395, 405, "P", oi=150, vol=100)])
    summ = summarize_chain(chain, "BTC", 85010)[0]
    assert summ.atm_strike == 85000 and summ.pcr_oi == pytest.approx(0.5) and summ.pcr_volume == pytest.approx(2.0)
    assert expiry_from_symbol("P-ETH-2630-011026") == pd.Timestamp("2026-10-01T12:00Z")


def test_iv_percentile_uses_only_past() -> None:
    idx = pd.date_range("2026-08-01T00:00Z", periods=24 * 60, freq="1h")
    hist = pd.Series(range(len(idx)), index=idx, dtype=float) / len(idx)
    now = idx[len(idx) // 2]
    p = iv_percentile(0.4, hist, now)
    assert 75 < p < 85  # 0.4 vs past values 0..0.5 -> ~80th percentile; future values ignored
    assert iv_percentile(0.4, hist.iloc[:10], idx[9]) is None
