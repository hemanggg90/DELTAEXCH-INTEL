from __future__ import annotations

import datetime as dt
import math

import pytest

from delta_intelligence.options import structures as st

EXP = dt.datetime(2026, 10, 3, 12, tzinfo=dt.timezone.utc)
NOW = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc)
CV = 0.001  # BTC options: 0.001 BTC per contract


def test_long_call_max_loss_is_premium_and_upside_uncapped() -> None:
    s = st.long_call("BTC", 85000, EXP, 100, CV)  # 100 contracts = 0.1 BTC
    s.entry_premiums = [500.0]
    assert s.net_premium() == pytest.approx(50.0)
    assert s.max_loss() == pytest.approx(50.0)
    assert math.isinf(s.max_profit())
    assert s.breakevens() == [pytest.approx(85500.0, abs=0.01)]
    assert s.pnl_at_expiry(87000) == pytest.approx(0.1 * 2000 - 50)


def test_long_put() -> None:
    s = st.long_put("BTC", 85000, EXP, 10, CV)
    s.entry_premiums = [400.0]
    assert s.max_loss() == pytest.approx(4.0)
    assert s.max_profit() == pytest.approx(0.01 * 85000 - 4.0)
    assert s.direction == "SHORT"


def test_bull_call_debit_spread() -> None:
    s = st.bull_call_spread("BTC", 85000, 86000, EXP, 100, CV)
    s.entry_premiums = [500.0, 200.0]  # pay 500, receive 200
    assert s.net_premium() == pytest.approx(30.0)
    assert s.max_loss() == pytest.approx(30.0)
    assert s.max_profit() == pytest.approx(0.1 * 1000 - 30.0)
    assert s.breakevens() == [pytest.approx(85300.0, abs=0.01)]


def test_credit_spreads_have_bounded_loss() -> None:
    s = st.bull_put_spread("BTC", k_short=85000, k_long=84000, expiry=EXP, contracts=100, cv=CV)
    s.entry_premiums = [150.0, 450.0]  # legs ordered [long 84000 P, short 85000 P]
    assert s.net_premium() == pytest.approx(-30.0)  # credit received
    assert s.max_profit() == pytest.approx(30.0)
    assert s.max_loss() == pytest.approx(0.1 * 1000 - 30.0)
    c = st.bear_call_spread("BTC", k_short=86000, k_long=87000, expiry=EXP, contracts=100, cv=CV)
    c.entry_premiums = [100.0, 300.0]
    assert c.max_loss() == pytest.approx(100 - 20) and c.max_profit() == pytest.approx(20)


def test_naked_shorts_are_impossible() -> None:
    with pytest.raises(st.UndefinedRiskError):
        st.Structure("NAKED", "BTC", [st.Leg("C", 85000, EXP, -1, 10)], CV)
    with pytest.raises(st.UndefinedRiskError):  # a long PUT does not cover a short CALL
        st.Structure("BAD", "BTC", [st.Leg("P", 84000, EXP, 1, 10), st.Leg("C", 86000, EXP, -1, 10)], CV)
    with pytest.raises(st.UndefinedRiskError):  # more short than long contracts
        st.Structure("BAD", "BTC", [st.Leg("C", 84000, EXP, 1, 5), st.Leg("C", 86000, EXP, -1, 10)], CV)


def test_builders_validate_strike_order_and_legs() -> None:
    with pytest.raises(ValueError):
        st.bull_call_spread("BTC", 86000, 85000, EXP, 1, CV)
    with pytest.raises(ValueError):
        st.bear_put_spread("BTC", 84000, 85000, EXP, 1, CV)
    with pytest.raises(ValueError):
        st.Leg("C", 85000, EXP.replace(tzinfo=None), 1, 1)
    with pytest.raises(ValueError):
        st.Structure("X", "BTC", [st.Leg("C", 85000, EXP, 1, 1), st.Leg("C", 86000, EXP + dt.timedelta(days=1), -1, 1)], CV)


def test_model_value_converges_to_payoff_at_expiry() -> None:
    s = st.bull_call_spread("BTC", 85000, 86000, EXP, 100, CV)
    assert s.model_value(85700, EXP, [0.4, 0.4]) == pytest.approx(s.payoff_at_expiry(85700))
    g = s.model_greeks(85500, NOW, [0.4, 0.42])
    assert 0 < g["delta"] < 0.1  # spread delta below a single 0.1-BTC long call's


def test_fees_with_premium_cap_and_gst() -> None:
    # cheap option: 0.01% of notional (85000*0.1 = 8.5) vs 3.5% of premium value (2*0.1*0.035 = 0.007): cap wins
    assert st.leg_fee(2.0, 85000, 0.1, 0.0001, 0.035, 0.18) == pytest.approx(0.007 * 1.18)
    # ATM option: notional fee 0.85 vs cap 3.5% * 1000 * 0.1 = 3.5: notional fee wins
    assert st.leg_fee(1000.0, 85000, 0.1, 0.0001, 0.035, 0.18) == pytest.approx(0.85 * 1.18)
    s = st.bull_call_spread("BTC", 85000, 86000, EXP, 100, CV)
    assert st.structure_fees(s, [500, 200], 85000, 0.0001, 0.035, 0.0) == pytest.approx(0.85 + 0.7)
