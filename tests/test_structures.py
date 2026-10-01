from __future__ import annotations

import datetime as dt
import math

import pytest

from delta_intelligence.brokers.order_guard import check_order
from delta_intelligence.options import structures as st

EXP = dt.datetime(2026, 10, 3, 12, tzinfo=dt.timezone.utc)
NOW = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc)
CV = 0.001


def test_long_call_max_loss_is_premium_and_upside_uncapped() -> None:
    s = st.long_call("BTC", 85000, EXP, 100, CV)
    s.entry_premiums = [500.0]
    assert s.premium_paid() == pytest.approx(50.0) and s.max_loss() == pytest.approx(50.0)
    assert math.isinf(s.max_profit())
    assert s.breakevens() == [pytest.approx(85500.0, abs=0.05)]


def test_long_put_and_straddle_strangle() -> None:
    p = st.long_put("BTC", 85000, EXP, 10, CV)
    p.entry_premiums = [400.0]
    assert p.max_loss() == pytest.approx(4.0) and p.max_profit() == pytest.approx(0.01 * 85000 - 4.0)
    sd = st.long_straddle("BTC", 85000, EXP, 10, CV)
    sd.entry_premiums = [500.0, 400.0]
    assert sd.max_loss() == pytest.approx(9.0) and math.isinf(sd.max_profit())
    assert [round(b) for b in sd.breakevens()] == [84100, 85900]
    sg = st.long_strangle("BTC", 84000, 86000, EXP, 10, CV)
    assert [lg.kind for lg in sg.legs] == ["C", "P"] and sg.direction == "VOL"
    with pytest.raises(ValueError):
        st.long_strangle("BTC", 86000, 84000, EXP, 1, CV)


@pytest.mark.parametrize("make", [
    lambda: st.Leg("C", 85000, EXP, -1, 1),
    lambda: st.Structure("BULL_CALL_DEBIT", "BTC", [st.Leg("C", 85000, EXP, 1, 1)], CV),
    lambda: st.Structure("LONG_CALL", "BTC", [st.Leg("C", 85000, EXP, -1, 1)], CV),
])
def test_short_legs_and_spreads_are_impossible(make) -> None:
    with pytest.raises(st.SellToOpenRejected):
        make()


def test_model_value_at_expiry_equals_payoff() -> None:
    s = st.long_straddle("BTC", 85000, EXP, 100, CV)
    assert s.model_value(85700, EXP, [0.4, 0.4]) == pytest.approx(s.payoff_at_expiry(85700))
    g = s.model_greeks(85000, NOW, [0.45, 0.45])
    assert abs(g["delta"]) < 0.02 and g["gamma"] > 0 and g["theta_day"] < 0


def test_fees_with_premium_cap_and_gst() -> None:
    assert st.leg_fee(2.0, 85000, 0.1, 0.0001, 0.035, 0.18) == pytest.approx(0.007 * 1.18)
    assert st.leg_fee(1000.0, 85000, 0.1, 0.0001, 0.035, 0.18) == pytest.approx(0.85 * 1.18)


# ---- broker-layer order guard ---------------------------------------------------------------------------------------
def test_order_guard_allows_only_buy_to_open_and_sell_to_close() -> None:
    check_order("buy", "C-BTC-85000-031026", 5, "OPEN", 0)
    check_order("sell", "C-BTC-85000-031026", 5, "CLOSE", 5)
    check_order("sell", "C-BTC-85000-031026", 2, "CLOSE", 5)


@pytest.mark.parametrize("side,size,purpose,held", [
    ("sell", 1, "OPEN", 0),  # sell to open
    ("sell", 1, "CLOSE", 0),  # nothing held
    ("sell", 6, "CLOSE", 5),  # more than held -> would go net short
    ("buy", 1, "CLOSE", 0),  # buy-to-close: never any shorts
])
def test_order_guard_rejections(side, size, purpose, held) -> None:
    with pytest.raises(st.SellToOpenRejected):
        check_order(side, "C-BTC-85000-031026", size, purpose, held)
