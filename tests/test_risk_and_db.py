from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, text

from delta_intelligence.config.settings import RiskLimits, Settings
from delta_intelligence.database import db
from delta_intelligence.database.models import RiskEvent, from_db_time, to_db_time
from delta_intelligence.risk.risk_engine import AccountState, LegQuote, ProposedTrade, evaluate_trade

ACCT = AccountState(equity=10_000, peak_equity=10_000, daily_pnl=0, trades_today=0, open_positions=0,
                    available_cash=10_000)
LEG = LegQuote("C-BTC-85000-031026", 1, bid=495, ask=505, open_interest=200, ask_size=20)
TRADE = ProposedTrade(strategy="Squeeze", underlying="BTC", bucket="CRYPTO_MAJORS", structure="LONG_CALL",
                      direction="LONG", premium_at_risk=45.0, cash_required=45.0, hours_to_expiry=26,
                      expected_hold_hours=4, legs=[LEG], relative_volume=1.2, iv_percentile=40)


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
        assert s.query(RiskEvent).filter_by(decision_id=d.decision_id).one().event_type == "APPROVED"


@pytest.mark.parametrize("acct_kw,trade_kw,needle", [
    ({"kill_switch": True}, {}, "kill switch"),
    ({"broker_connected": False}, {}, "disconnected"),
    ({}, {"data_quality_status": "DEGRADED"}, "Data quality"),
    ({}, {"legs": [replace(LEG, side=-1)]}, "Sell-to-open"),
    ({"daily_pnl": -200}, {}, "Daily loss"),
    ({"equity": 9_100, "available_cash": 9_100}, {}, "Drawdown"),
    ({"trades_today": 6}, {}, "trades per day"),
    ({"open_positions": 3}, {}, "concurrent"),
    ({}, {"premium_at_risk": 60.0, "cash_required": 60}, "exceeds 0.5%"),
    ({"exposure_by_bucket": {"CRYPTO_MAJORS": 120}}, {}, "CRYPTO_MAJORS"),
    ({"available_cash": 40}, {}, "Insufficient funds"),
    ({}, {"relative_volume": 0.1}, "relative volume"),
    ({}, {"hours_to_expiry": 5.5}, "expiry guard"),
    ({}, {"legs": [replace(LEG, bid=90, ask=110)]}, "spread 20.0%"),
    ({}, {"legs": [replace(LEG, open_interest=50)]}, "open interest"),
    ({}, {"legs": [replace(LEG, ask_size=3)]}, "offered"),
    ({}, {"iv_percentile": 92}, "IV percentile"),
    ({}, {"passes_breakeven": False, "breakeven_detail": "move 300 < required 900"}, "Breakeven"),
])
def test_each_veto(acct_kw, trade_kw, needle) -> None:
    d = evaluate_trade(replace(ACCT, **acct_kw), replace(TRADE, **trade_kw), persist=False)
    assert not d.approved and needle in d.reason, d.reason


def test_kill_switch_wins_over_everything() -> None:
    d = evaluate_trade(replace(ACCT, kill_switch=True, daily_pnl=-9999),
                       replace(TRADE, premium_at_risk=1e9, legs=[replace(LEG, side=-1)]), persist=False)
    assert "kill switch" in d.reason


def test_bucket_cap_does_not_apply_to_xaut() -> None:
    t = replace(TRADE, underlying="XAUT", bucket="GOLD")
    assert evaluate_trade(replace(ACCT, exposure_by_bucket={"CRYPTO_MAJORS": 140}), t, persist=False).approved


def test_event_strategy_exempt_from_iv_gate() -> None:
    t = replace(TRADE, iv_percentile=95, is_event_strategy=True, structure="LONG_STRADDLE", direction="VOL")
    assert evaluate_trade(ACCT, t, persist=False).approved


def test_premium_cap_cannot_exceed_one_percent_via_env() -> None:
    assert Settings.from_env({"MAX_PREMIUM_PER_TRADE_PCT": "5"}).risk.max_premium_per_trade_pct == 1.0
    assert Settings.from_env({}).risk.allow_sell_to_open is False


def test_user_approved_v3_defaults() -> None:
    r = RiskLimits()
    assert (r.max_premium_per_trade_pct, r.min_leg_open_interest, r.min_leg_quote_size, r.expiry_guard_hours,
            r.correlated_bucket_cap_pct, r.premium_stop_pct) == (0.5, 100, 10, 2.0, 1.5, 35.0)
    assert (r.max_strategy_exposure_pct, r.max_portfolio_exposure_pct, r.max_daily_loss_pct) == (10.0, 20.0, 2.0)


def test_db_additive_migration_and_state(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    eng = create_engine(url)
    with eng.begin() as c:
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
    assert to_db_time(t) == dt.datetime(2026, 10, 1, 13, 0)
    assert from_db_time(to_db_time(t)).tzinfo is dt.timezone.utc
    with pytest.raises(ValueError):
        to_db_time(dt.datetime(2026, 1, 1))


def test_persistence_helpers_are_noops_before_init() -> None:
    db.reset_engine()
    db.persist_system_event("x", "INFO", "nothing should be written")
    assert not db.is_initialized()
