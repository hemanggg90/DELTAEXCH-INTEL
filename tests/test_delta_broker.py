"""P7: DeltaBroker, the LIVE startup gate, reconciliation and live-mode wiring. All HTTP is mocked."""
from __future__ import annotations

import datetime as dt
import json
import re

import pandas as pd
import pytest
import requests
import responses

from delta_intelligence.brokers.cache import TtlCache
from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.brokers.delta_broker import DeltaBroker, LiveNotAuthorizedError, price_string
from delta_intelligence.brokers.live_gate import LiveGateError, build_live_broker, gate_requirements
from delta_intelligence.brokers.paper_broker import TradePlan
from delta_intelligence.brokers.rate_limit import RateLimiter
from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, DeltaLimits, Settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg
from delta_intelligence.execution.account import AccountTracker
from delta_intelligence.execution.engine import kill_switch_on, set_kill_switch
from delta_intelligence.execution.recovery import recover
from delta_intelligence.options.chain import OptionChain, OptionQuote
from delta_intelligence.options.structures import Leg, SellToOpenRejected, long_call

BASE = "https://api.live.test"
EXP = pd.Timestamp("2026-10-03T12:00Z")
NOW = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc)
C85 = "C-BTC-85000-031026"
ENV = {"TRADING_MODE": "LIVE", "TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE, "DELTA_API_KEY": "k-test-1",
       "DELTA_API_SECRET": "s-test-1", "DELTA_BASE_URL": BASE, "DELTA_ENV": "TESTNET"}


def quote(symbol=C85, bid=495.0, ask=505.0):
    return OptionQuote(symbol, "BTC", "C", 85000.0, EXP, bid, ask, None if bid is None else (bid + ask) / 2, 0.45, 100.0, 10.0, 10.0, 0.5,
                       85000.0, 7, 0.001, None, 10.0, 0.1)


def client_for(fake_clock, **kw) -> DeltaClient:
    limiter = RateLimiter(DeltaLimits(), clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock)
    return DeltaClient(BASE, "k-test-1", "s-test-1", environment="TESTNET", limiter=limiter, allow_orders=True,
                       clock=fake_clock, sleep=fake_clock.sleep, ticker_cache=TtlCache(fake_clock),
                       product_cache=TtlCache(fake_clock), **kw)


@pytest.fixture
def live(tmp_path, fake_clock, mocked):
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'l.db').as_posix()}")
    s = Settings.from_env({**ENV, "SLIPPAGE_TICKS": "1"})
    box = {"chain": OptionChain([quote()]), "orders": [], "responses": []}
    b = DeltaBroker(lambda: box["chain"], s, client=client_for(fake_clock), clock=lambda: NOW)

    def on_order(request):
        body = json.loads(request.body)
        box["orders"].append(body)
        r = box["responses"].pop(0) if box["responses"] else {}
        if isinstance(r, Exception):
            raise r
        res = {"id": 1000 + len(box["orders"]), "size": body["size"], "unfilled_size": 0, "state": "closed",
               "average_fill_price": float(body["limit_price"]), "paid_commission": 0.05, **r}
        return 200, {}, json.dumps({"success": True, "result": res})

    mocked.add_callback(responses.POST, f"{BASE}/v2/orders", callback=on_order, content_type="application/json")
    b.box, b.rsps = box, mocked
    yield b
    db.reset_engine()


def plan(contracts=10):
    return TradePlan("Momentum", long_call("BTC", 85000, EXP.to_pydatetime(), contracts, 0.001, C85), 84000.0,
                     87000.0, premium_stop_pct=35.0)


# ---- gates ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("override", [{"TRADING_LIVE_CONFIRM": ""}, {"TRADING_MODE": "PAPER"},
                                      {"DELTA_API_KEY": ""}, {"DELTA_API_SECRET": ""}])
def test_broker_needs_both_gates_and_credentials(override, fake_clock) -> None:
    s = Settings.from_env({**ENV, **override})
    with pytest.raises(LiveNotAuthorizedError):
        DeltaBroker(lambda: OptionChain([]), s, client=client_for(fake_clock))


def test_broker_refuses_a_client_that_cannot_send_orders_or_points_elsewhere(fake_clock) -> None:
    s = Settings.from_env(ENV)
    ro = DeltaClient(BASE, "k", "s", environment="TESTNET", allow_orders=False)
    with pytest.raises(LiveNotAuthorizedError):
        DeltaBroker(lambda: OptionChain([]), s, client=ro)
    other = DeltaClient("https://elsewhere.test", "k", "s", environment="TESTNET", allow_orders=True)
    with pytest.raises(LiveNotAuthorizedError):
        DeltaBroker(lambda: OptionChain([]), s, client=other)


def test_settings_default_never_builds_a_live_broker() -> None:
    s = Settings.from_env({})
    assert s.trading_mode == "PAPER" and s.delta_env == "TESTNET"
    with pytest.raises(LiveGateError):
        build_live_broker(lambda: OptionChain([]), s)


def test_gate_requirements_listing() -> None:
    unmet = [n for n, ok, _ in gate_requirements(Settings.from_env({"TRADING_MODE": "LIVE"})) if not ok]
    assert any("CONFIRM" in n for n in unmet) and any("API key" in n for n in unmet)
    assert all(ok for _, ok, _ in gate_requirements(Settings.from_env(ENV)))


def test_gate_refuses_when_connectivity_fails(monkeypatch, fake_clock) -> None:
    from delta_intelligence.brokers import live_gate
    from delta_intelligence.brokers.connectivity import CheckResult

    s = Settings.from_env(ENV)
    monkeypatch.setattr(live_gate, "run_connectivity_check",
                        lambda *_a, **_k: [CheckResult("authentication + IP whitelist", False, "IP not whitelisted")])
    with pytest.raises(LiveGateError, match="IP not whitelisted"):
        build_live_broker(lambda: OptionChain([]), s, client=client_for(fake_clock))
    monkeypatch.setattr(live_gate, "run_connectivity_check", lambda *_a, **_k: [CheckResult("ok", True, "fine")])
    assert isinstance(build_live_broker(lambda: OptionChain([]), s, client=client_for(fake_clock)), DeltaBroker)


# ---- price formatting ------------------------------------------------------------------------------------------------
def test_price_string_rounds_on_the_tick_grid() -> None:
    assert price_string(505.1, 0.1, buy=True) == "505.1"
    assert price_string(505.13, 0.1, buy=True) == "505.2"  # buys round up so they still cross
    assert price_string(494.87, 0.1, buy=False) == "494.8"  # sells round down
    assert price_string(0.001, 0.5, buy=False) == "0.5"  # never below one tick
    assert price_string(2000.0, 0.01, buy=True) == "2000"


# ---- entry ---------------------------------------------------------------------------------------------------------
def test_buy_sends_one_ioc_limit_order_and_records_everything(live) -> None:
    r = live.open_structure(plan())
    assert r.ok and not r.incomplete
    assert len(live.box["orders"]) == 1
    o = live.box["orders"][0]
    assert (o["side"], o["order_type"], o["time_in_force"], o["reduce_only"]) == ("buy", "limit_order", "ioc", False)
    assert o["limit_price"] == "505.1" and o["product_id"] == 7 and len(o["client_order_id"]) == 32
    with db.get_session() as s:
        order = s.query(Order).one()
        pos = s.query(Position).one()
        assert (order.mode, order.status, order.purpose, order.reduce_only) == ("LIVE", "FILLED", "OPEN", False)
        assert order.client_order_id == o["client_order_id"] and order.exchange_order_id == "1001"
        assert pos.mode == "LIVE" and pos.environment == "TESTNET" and pos.status == "OPEN"
        assert pos.entry_net_premium == pytest.approx(505.1 * 10 * 0.001)
        assert pos.fees == pytest.approx(0.05) and pos.premium_stop_value == pytest.approx(pos.entry_net_premium * 0.65)
        assert pos.max_loss == pytest.approx(pos.entry_net_premium + 0.05)
        assert s.query(Fill).one().size == 10


def test_no_fill_creates_no_position(live) -> None:
    live.box["responses"].append({"unfilled_size": 10, "state": "cancelled"})
    r = live.open_structure(plan())
    assert not r.ok
    with db.get_session() as s:
        assert s.query(Position).count() == 0 and s.query(Order).one().status == "CANCELLED"


def test_partial_fill_keeps_what_was_bought_and_flags_it(live) -> None:
    live.box["responses"].append({"unfilled_size": 4})
    r = live.open_structure(plan())
    assert r.ok and r.incomplete
    with db.get_session() as s:
        pos, leg = s.query(Position).one(), s.query(PositionLeg).one()
        assert pos.incomplete and leg.contracts == 6 and pos.contracts == 6
        assert pos.entry_net_premium == pytest.approx(505.1 * 6 * 0.001)


def test_missing_price_and_fee_in_response_use_conservative_fallbacks(live) -> None:
    live.box["responses"].append({"average_fill_price": None, "paid_commission": None})
    assert live.open_structure(plan()).ok
    with db.get_session() as s:
        f, o = s.query(Fill).one(), s.query(Order).one()
        assert f.price == pytest.approx(505.1) and f.fee > 0
        assert "limit price used" in o.reject_reason and "fee estimated" in o.reject_reason


def test_no_quote_means_no_http_call(live) -> None:
    live.box["chain"] = OptionChain([quote(bid=None, ask=None)])
    assert not live.open_structure(plan()).ok
    assert live.box["orders"] == []


def test_exchange_rejection_is_recorded_not_retried(live) -> None:
    live.rsps.replace(responses.POST, f"{BASE}/v2/orders", status=400,
                      json={"success": False, "error": {"code": "invalid_order"}})
    assert not live.open_structure(plan()).ok
    with db.get_session() as s:
        assert s.query(Order).one().status == "REJECTED"
    assert sum(1 for c in live.rsps.calls if c.request.method == "POST") == 1


# ---- exit ----------------------------------------------------------------------------------------------------------
def test_close_is_reduce_only_ioc_and_pnl_uses_proceeds(live) -> None:
    pid = live.open_structure(plan()).position_id
    live.box["chain"] = OptionChain([quote(bid=600.0, ask=610.0)])
    pnl = live.close_position(pid, "TARGET")
    sell = live.box["orders"][1]
    assert (sell["side"], sell["reduce_only"], sell["time_in_force"], sell["limit_price"]) == ("sell", True, "ioc", "599.9")
    paid, got = 505.1 * 10 * 0.001, 599.9 * 10 * 0.001
    assert pnl == pytest.approx(got - paid - 0.10)
    with db.get_session() as s:
        p = s.query(Position).one()
        assert p.status == "CLOSED" and p.exit_reason == "TARGET" and p.realized_pnl == pytest.approx(pnl)
        assert s.query(PositionLeg).one().status == "CLOSED"


def test_partial_close_keeps_the_rest_open_then_finishes(live) -> None:
    pid = live.open_structure(plan()).position_id
    live.box["chain"] = OptionChain([quote(bid=600.0, ask=610.0)])
    live.box["responses"].append({"unfilled_size": 3})
    assert live.close_position(pid, "TIME_STOP") is None  # 7 of 10 sold
    with db.get_session() as s:
        assert s.query(PositionLeg).one().contracts == 3 and s.query(Position).one().status == "OPEN"
    pnl = live.close_position(pid, "TIME_STOP")
    assert [o["size"] for o in live.box["orders"][1:]] == [10, 3]
    assert pnl == pytest.approx(599.9 * 10 * 0.001 - 505.1 * 10 * 0.001 - 0.15)


def test_sell_to_open_is_impossible_and_sends_nothing(live) -> None:
    chain = live.chain()
    leg = Leg("C", 85000.0, EXP.to_pydatetime(), 1, 5, C85)
    with pytest.raises(SellToOpenRejected):
        live._send_leg("POS-x", leg, False, 0.001, "CLOSE", chain, held_long=0)  # nothing held
    with pytest.raises(SellToOpenRejected):
        live._send_leg("POS-x", leg, False, 0.001, "OPEN", chain, held_long=5)  # sell marked as an open
    with pytest.raises(SellToOpenRejected):
        live._send_leg("POS-x", leg, False, 0.001, "CLOSE", chain, held_long=2)  # more than held
    assert live.box["orders"] == []


def test_settle_waits_for_the_exchange_then_records_modelled_result(live) -> None:
    pid = live.open_structure(plan()).position_id
    held = [{"product_symbol": C85, "size": 10}]
    live.rsps.add(responses.GET, f"{BASE}/v2/positions/margined", json={"success": True, "result": held})
    assert live.settle_position(pid, 86000.0) is None  # exchange still shows the leg
    live.rsps.replace(responses.GET, f"{BASE}/v2/positions/margined", json={"success": True, "result": []})
    pnl = live.settle_position(pid, 86000.0)
    assert pnl == pytest.approx(1000.0 * 10 * 0.001 - 505.1 * 10 * 0.001 - 0.05)
    with db.get_session() as s:
        assert s.query(Position).one().status == "SETTLED" and "modelled" in s.query(Position).one().notes


# ---- unknown order state -------------------------------------------------------------------------------------------------
def test_timeout_is_resolved_through_client_order_id_without_resending(live) -> None:
    live.box["responses"].append(requests.ConnectTimeout())
    live.rsps.add(responses.GET, re.compile(rf"{BASE}/v2/orders/client_order_id/.*"),
                  json={"success": True, "result": {"id": 77, "state": "closed", "size": 10, "unfilled_size": 0,
                                                    "average_fill_price": "505.1", "paid_commission": 0.05}})
    assert live.open_structure(plan()).ok
    assert len(live.box["orders"]) == 1  # never auto-retried
    with db.get_session() as s:
        o = s.query(Order).one()
        assert o.status == "FILLED" and "resolved by client_order_id" in o.reject_reason
    assert not kill_switch_on()


def test_unresolvable_timeout_marks_unknown_and_engages_kill_switch(live) -> None:
    live.box["responses"].append(requests.ReadTimeout())
    live.rsps.add(responses.GET, re.compile(rf"{BASE}/v2/orders/client_order_id/.*"), status=404,
                  json={"success": False, "error": "not_found"})
    r = live.open_structure(plan())
    assert not r.ok and len(live.box["orders"]) == 1
    with db.get_session() as s:
        assert s.query(Order).one().status == "UNKNOWN" and s.query(Position).count() == 0
    assert kill_switch_on() and not live.is_connected()


# ---- account -------------------------------------------------------------------------------------------------------------
def test_account_snapshot_comes_from_the_wallet_plus_open_value(live) -> None:
    live.rsps.add(responses.GET, f"{BASE}/v2/wallet/balances", json={"success": True, "result": [
        {"asset_symbol": "USDT", "balance": "4000", "available_balance": "3900"},
        {"asset_symbol": "BTC", "balance": "1", "available_balance": "1"}]})
    live.open_structure(plan())
    snap = live.account_snapshot()
    assert snap["cash"] == 4000 and snap["available_cash"] == 3900 and snap["open_positions"] == 1
    assert snap["open_value"] == pytest.approx(500.0 * 10 * 0.001)  # mid 500
    assert snap["equity"] == pytest.approx(4005.0) and snap["total_exposure"] > 0


def test_account_tracker_is_per_mode_and_counts_the_right_book(live) -> None:
    s = live.settings
    tr = AccountTracker(s)
    tr.update({"equity": 10_000.0}, NOW, "PAPER")
    st = tr.update({"equity": 500.0}, NOW, "LIVE")
    assert st["peak_equity"] == 500.0  # the paper peak never leaks into LIVE
    live.open_structure(plan())
    assert tr.trades_today(NOW, "LIVE") == 1 and tr.trades_today(NOW, "PAPER") == 0


# ---- reconcile ---------------------------------------------------------------------------------------------------------------
def exchange_positions(live, rows):
    live.rsps.add(responses.GET, f"{BASE}/v2/positions/margined", json={"success": True, "result": rows})


def test_reconcile_agreeing_book_is_ok(live) -> None:
    live.open_structure(plan())
    exchange_positions(live, [{"product_symbol": C85, "size": 10}])
    r = live.reconcile()
    assert r["ok"] and r["critical"] == [] and not kill_switch_on()


def test_reconcile_orphan_short_and_size_mismatch_are_critical(live) -> None:
    live.open_structure(plan())
    exchange_positions(live, [{"product_symbol": C85, "size": 4},
                              {"product_symbol": "P-BTC-80000-031026", "size": 3},
                              {"product_symbol": "C-ETH-3000-031026", "size": -2}])
    r = live.reconcile()
    text = " | ".join(r["critical"])
    assert not r["ok"] and "SIZE MISMATCH" in text and "ORPHAN" in text and "SHORT" in text
    assert kill_switch_on()


def test_reconcile_marks_position_stale_when_exchange_is_flat(live) -> None:
    live.open_structure(plan())
    exchange_positions(live, [])
    r = live.reconcile()
    assert r["ok"] and any("CLOSED_ELSEWHERE" in n for n in r["notes"])
    with db.get_session() as s:
        assert s.query(Position).one().status == "STALE"
    assert live.open_positions() == []


def test_reconcile_resolves_unknown_orders(live) -> None:
    live.box["responses"].append(requests.ConnectTimeout())
    live.rsps.add(responses.GET, re.compile(rf"{BASE}/v2/orders/client_order_id/.*"), status=404,
                  json={"success": False, "error": "not_found"})
    live.open_structure(plan())  # leaves one UNKNOWN order and the kill switch on
    live.rsps.replace(responses.GET, re.compile(rf"{BASE}/v2/orders/client_order_id/.*"), status=404,
                      json={"success": False, "error": "not_found"})
    exchange_positions(live, [])
    r = live.reconcile()
    assert r["ok"]
    with db.get_session() as s:
        assert s.query(Order).one().status == "REJECTED"


def test_recovery_runs_reconcile_in_live_mode_and_traps_failures(live) -> None:
    exchange_positions(live, [{"product_symbol": "C-BTC-90000-031026", "size": 1}])
    out = recover(live, None, NOW)
    assert out["reconcile"]["ok"] is False and kill_switch_on()
    set_kill_switch(False)

    class Broken(DeltaBroker):
        def reconcile(self):
            raise RuntimeError("exchange unreachable")

    b = Broken(live.chain, live.settings, client=live.client, clock=lambda: NOW)
    out = recover(b, None, NOW)
    assert out["reconcile"]["ok"] is False and kill_switch_on()


# ---- real testnet payload shapes (captured 2026-10-02, trimmed) ----------------------------------------------------
REAL_BUY = {"unfilled_size": 0, "size": 1, "state": "closed", "id": 2174316493, "paid_commission": "0.0059",
            "average_fill_price": "50", "limit_price": "50.5", "product_symbol": C85, "time_in_force": "ioc",
            "reduce_only": False, "client_order_id": "x", "side": "buy"}
REAL_SELL = {**REAL_BUY, "id": 2174316498, "side": "sell", "reduce_only": True, "paid_commission": "0.00472",
             "average_fill_price": "40", "limit_price": "39.5"}


def test_real_payload_shapes_with_string_numbers_round_trip(live) -> None:
    live.box["chain"] = OptionChain([quote(bid=40.0, ask=50.0)])
    live.box["responses"] += [REAL_BUY, REAL_SELL]
    pid = live.open_structure(plan(1)).position_id
    pnl = live.close_position(pid, "TEST")
    # matches the exchange: wallet moved 200 -> 199.97938
    assert pnl == pytest.approx(-0.02062)
    with db.get_session() as s:
        assert [f.fee for f in s.query(Fill).order_by(Fill.id)] == [0.0059, 0.00472]
        assert s.query(Position).one().exit_value == pytest.approx(0.04)


def test_positions_row_uses_top_level_product_symbol(live) -> None:
    live.open_structure(plan())
    exchange_positions(live, [{"size": 10, "product_symbol": C85, "product_id": 7, "entry_price": "505",
                               "product": {"symbol": C85}}])
    assert live.reconcile()["ok"]
