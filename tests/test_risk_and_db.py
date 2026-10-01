from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, text

from delta_intelligence.config.settings import RiskLimits
from delta_intelligence.database import db
from delta_intelligence.database.models import RiskEvent, from_db_time, to_db_time
from delta_intelligence.risk.risk_engine import AccountState, LegQuote, ProposedTrade, evaluate_trade

ACCT = AccountState(equity=10_000, peak_equity=10_000, daily_pnl=0, trades_today=0, open_positions=0,
                    available_cash=10_000)
LEGS = [LegQuote("C-BTC-85000-031026", 1, bid=495, ask=505, open_interest=200)]
TRADE = ProposedTrade(strategy="Momentum", underlying="BTC", structure="LONG_CALL", direction="LONG", max_loss=45.0,
                      net_premium=44.0, cash_required=45.0, hours_to_expiry=10, legs=LEGS, relative_volume=1.2,
                      atm_iv=0.45, realized_vol=0.40)


@pytest.fixture
def temp_db(tmp_path):
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    yield
    db.reset_engine()


def test_approves_clean_trade_and_persists(temp_db) -> None:
    d = evaluate_trade(ACCT, TRADE)
    assert d.approved and all(d.checks.values())
    with db.get_session() as s:
        ev = s.query(RiskEvent).filter_by(decision_id=d.decision_id).one()
        assert ev.event_type == "APPROVED" and ev.details["structure"] == "LONG_CALL"


@pytest.mark.parametrize("acct_kw,trade_kw,needle", [
    ({"kill_switch": True}, {}, "kill switch"),
    ({"broker_connected": False}, {}, "disconnected"),
    ({}, {"data_quality_status": "DEGRADED"}, "Data quality"),
    ({"daily_pnl": -200}, {}, "Daily loss"),
    ({"equity": 9_100, "available_cash": 9_100}, {}, "Drawdown"),
    ({"trades_today": 6}, {}, "trades per day"),
    ({"open_positions": 3}, {}, "concurrent"),
    ({}, {"defined_risk": False}, "naked"),
    ({}, {"max_loss": 60.0, "cash_required": 60}, "exceeds 0.5%"),
    ({"exposure_by_strategy": {"Momentum": 980}}, {}, "Strategy exposure"),
    ({"total_exposure": 1_980}, {}, "Portfolio exposure"),
    ({"available_cash": 40}, {}, "Insufficient funds"),
    ({}, {"relative_volume": 0.1}, "relative volume"),
    ({}, {"hours_to_expiry": 5.5}, "to expiry"),
    ({}, {"legs": [LegQuote("X", 1, bid=90, ask=110)]}, "spread 20.0%"),
    ({}, {"legs": [LegQuote("X", 1, bid=None, ask=110)]}, "no two-sided quote"),
    ({}, {"atm_iv": 0.9, "realized_vol": 0.4}, "Implied vol is 2.25x"),
])
def test_each_veto(acct_kw, trade_kw, needle) -> None:
    d = evaluate_trade(replace(ACCT, **acct_kw), replace(TRADE, **trade_kw), persist=False)
    assert not d.approved and needle in d.reason


def test_kill_switch_wins_over_everything() -> None:
    d = evaluate_trade(replace(ACCT, kill_switch=True, daily_pnl=-9999), replace(TRADE, max_loss=1e9), persist=False)
    assert "kill switch" in d.reason


def test_credit_spread_skips_iv_rule() -> None:
    credit = replace(TRADE, structure="BULL_PUT_CREDIT", net_premium=-20.0, atm_iv=0.9, realized_vol=0.3)
    assert evaluate_trade(ACCT, credit, persist=False).approved


def test_open_interest_check_only_when_configured() -> None:
    thin = replace(TRADE, legs=[LegQuote("X", 1, bid=495, ask=505, open_interest=3)])
    assert evaluate_trade(ACCT, thin, persist=False).approved  # OFF by default
    lim = RiskLimits(min_leg_open_interest=50)
    d = evaluate_trade(ACCT, thin, limits=lim, persist=False)
    assert not d.approved and "open interest" in d.reason


def test_db_additive_migration_and_state(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    eng = create_engine(url)
    with eng.begin() as c:  # an "old" positions table missing most columns
        c.execute(text("CREATE TABLE positions (id INTEGER PRIMARY KEY, position_id VARCHAR(32), mode VARCHAR(8), "
                       "underlying VARCHAR(16), structure VARCHAR(24), expiry DATETIME, status VARCHAR(12))"))
    eng.dispose()
    db.reset_engine()
    added = db.init_db(url)
    assert "positions.max_loss" in added and "positions.incomplete" in added
    db.set_state("kill_switch", {"on": True})
    assert db.get_state("kill_switch") == {"on": True} and db.get_state("missing", 7) == 7
    db.reset_engine()


def test_db_times_are_utc_and_naive_rejected() -> None:
    t = dt.datetime(2026, 10, 1, 18, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    stored = to_db_time(t)
    assert stored == dt.datetime(2026, 10, 1, 13, 0) and from_db_time(stored).tzinfo is dt.timezone.utc
    with pytest.raises(ValueError):
        to_db_time(dt.datetime(2026, 1, 1))


def test_persistence_helpers_are_noops_before_init() -> None:
    db.reset_engine()
    db.persist_system_event("x", "INFO", "nothing should be written")  # must not create a DB or raise
    assert not db.is_initialized()
