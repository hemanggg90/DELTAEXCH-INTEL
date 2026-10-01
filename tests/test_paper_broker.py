from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from delta_intelligence.brokers.paper_broker import PaperBroker, TradePlan
from delta_intelligence.config.settings import Settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg
from delta_intelligence.execution.sizing import per_contract_max_loss, size_contracts
from delta_intelligence.options.chain import OptionChain, OptionQuote, expiry_from_symbol, parse_ticker
from delta_intelligence.options.structures import bear_put_spread, bull_call_spread, bull_put_spread, long_call

EXP = pd.Timestamp("2026-10-03T12:00Z")
NOW = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc)
S = 85000.0


def q(symbol, bid, ask, kind="C", strike=85000):
    return OptionQuote(symbol, "BTC", kind, float(strike), EXP, bid, ask, None if bid is None else (bid + ask) / 2,
                       0.45, 100.0, 10.0, 10.0, 0.5, S, 1, 0.001, None)


@pytest.fixture
def broker(tmp_path):
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    quotes = {"chain": OptionChain([q("C-BTC-85000-031026", 495, 505), q("C-BTC-86000-031026", 195, 205, strike=86000),
                                    q("P-BTC-85000-031026", 395, 405, "P"), q("P-BTC-84000-031026", 95, 105, "P", 84000)])}
    s = Settings.from_env({"PAPER_STARTING_CAPITAL_USD": "10000", "GST_RATE": "0.18", "SLIPPAGE_TICKS": "1"})
    b = PaperBroker(lambda: quotes["chain"], s, clock=lambda: NOW)
    b._quotes = quotes
    yield b
    db.reset_engine()


def with_symbols(st):
    for leg in st.legs:
        object.__setattr__(leg, "symbol", f"{leg.kind}-BTC-{int(leg.strike)}-031026")
    return st


def test_long_call_fills_at_ask_plus_slippage_and_moves_cash(broker) -> None:
    st = with_symbols(long_call("BTC", 85000, EXP.to_pydatetime(), 100, 0.001))
    r = broker.open_structure(TradePlan("Momentum", st))
    assert r.ok and not r.incomplete
    price = r.fills[0][2]
    assert price == pytest.approx(505.1)
    fee = min(0.0001 * S * 0.1, 0.035 * 505.1 * 0.1) * 1.18
    assert broker.cash == pytest.approx(10_000 - 50.51 - fee)
    with db.get_session() as s:
        o = s.query(Order).one()
        assert len(o.client_order_id) == 32 and o.side == "buy" and o.status == "FILLED"
        assert s.query(Fill).one().gst == pytest.approx(fee - fee / 1.18)


def test_spread_opens_long_first_and_closes_short_first(broker) -> None:
    st = with_symbols(bull_call_spread("BTC", 85000, 86000, EXP.to_pydatetime(), 10, 0.001))
    r = broker.open_structure(TradePlan("Momentum", st))
    assert [f[1] for f in r.fills] == [1, -1]
    broker._quotes["chain"] = OptionChain([q("C-BTC-85000-031026", 695, 705), q("C-BTC-86000-031026", 295, 305, strike=86000)])
    pnl = broker.close_position(r.position_id, "TARGET")
    with db.get_session() as s:
        closes = s.query(Order).filter_by(purpose="CLOSE").order_by(Order.id).all()
        assert [o.side for o in closes] == ["buy", "sell"]  # buy back the short call, then sell the long
        pos = s.query(Position).one()
        assert pos.status == "CLOSED" and pos.realized_pnl == pytest.approx(pnl)
    assert pnl == pytest.approx((694.9 - 505.1 - (305.1 - 194.9)) * 0.01 - pos.fees, abs=1e-6)
    assert broker.cash == pytest.approx(10_000 + pnl)


def test_failed_short_leg_keeps_long_and_flags_incomplete(broker) -> None:
    broker._quotes["chain"] = OptionChain([q("C-BTC-85000-031026", 495, 505), q("C-BTC-86000-031026", None, None, strike=86000)])
    st = with_symbols(bull_call_spread("BTC", 85000, 86000, EXP.to_pydatetime(), 10, 0.001))
    r = broker.open_structure(TradePlan("Momentum", st))
    assert r.ok and r.incomplete
    with db.get_session() as s:
        pos = s.query(Position).one()
        assert pos.structure == "LONG_CALL" and pos.incomplete
        assert {lg.status for lg in s.query(PositionLeg).all()} == {"OPEN", "FAILED"}


def test_unfillable_long_leg_aborts(broker) -> None:
    broker._quotes["chain"] = OptionChain([q("C-BTC-85000-031026", None, None)])
    r = broker.open_structure(TradePlan("Momentum", with_symbols(long_call("BTC", 85000, EXP.to_pydatetime(), 1, 0.001))))
    assert not r.ok and broker.cash == 10_000
    with db.get_session() as s:
        assert s.query(Position).count() == 0 and s.query(Order).one().status == "REJECTED"


def test_credit_spread_reserves_margin(broker) -> None:
    st = with_symbols(bull_put_spread("BTC", 85000, 84000, EXP.to_pydatetime(), 10, 0.001))
    r = broker.open_structure(TradePlan("RSI", st))
    snap = broker.account_snapshot()
    with db.get_session() as s:
        pos = s.query(Position).one()
    assert pos.entry_net_premium < 0 and pos.reserved_margin == pytest.approx(pos.max_loss)
    assert snap["available_cash"] == pytest.approx(snap["cash"] - pos.max_loss)
    assert snap["total_exposure"] == pytest.approx(pos.max_loss)


def test_settlement_at_intrinsic(broker) -> None:
    st = with_symbols(bear_put_spread("BTC", 85000, 84000, EXP.to_pydatetime(), 10, 0.001))
    r = broker.open_structure(TradePlan("X", st))
    pnl = broker.settle_position(r.position_id, settlement_index=83500.0)
    with db.get_session() as s:
        pos = s.query(Position).one()
    # width 1000 * 0.01 BTC = $10 payoff
    assert pos.status == "SETTLED" and pos.exit_value == pytest.approx(10.0)
    assert pnl == pytest.approx(10.0 - pos.entry_net_premium - pos.fees)


def test_book_survives_restart(broker, tmp_path) -> None:
    r = broker.open_structure(TradePlan("M", with_symbols(long_call("BTC", 85000, EXP.to_pydatetime(), 10, 0.001))))
    cash = broker.cash
    fresh = PaperBroker(broker.chain, broker.settings, clock=broker.clock)
    assert fresh.cash == pytest.approx(cash)
    assert [it["position"].position_id for it in fresh.open_positions()] == [r.position_id]
    v, upnl = fresh.mark(fresh.open_positions()[0])
    assert v == pytest.approx(5.0) and upnl == pytest.approx(-0.051, abs=1e-9)


def test_sizing() -> None:
    st = long_call("BTC", 85000, EXP.to_pydatetime(), 1, 0.001)
    per = per_contract_max_loss(st, [505.0], S, 0.0001, 0.035, 0.18)
    assert per == pytest.approx(0.505 + 2 * min(0.0085, 0.035 * 0.505) * 1.18)
    n = size_contracts(10_000, 0.5, per, cash_available=10_000, cash_per_contract=per)
    assert n == int(0.9 * 50 / per) and size_contracts(100, 0.5, per, 100, per) == 0


def test_chain_parsing() -> None:
    t = {"symbol": "C-BTC-87000-021026", "strike_price": "87000", "mark_price": "26.3", "product_id": 5,
         "contract_value": "0.001", "oi_contracts": "469", "quotes": {"best_bid": "10.6", "best_ask": "12",
         "mark_iv": "0.35", "bid_size": "1000", "ask_size": "6037"}, "greeks": {"delta": "0.048", "spot": "84764.4"}}
    oq = parse_ticker(t)
    assert oq.expiry == pd.Timestamp("2026-10-02T12:00Z") and oq.mid == pytest.approx(11.3) and oq.kind == "C"
    assert parse_ticker({"symbol": "BTCUSD"}) is None
    chain = OptionChain([oq])
    assert chain.symbol("BTC", "C", 87000.0, oq.expiry) == "C-BTC-87000-021026"
    assert list(chain.strikes("BTC", oq.expiry)) == [87000.0]
    assert expiry_from_symbol("P-ETH-2630-011026") == pd.Timestamp("2026-10-01T12:00Z")
