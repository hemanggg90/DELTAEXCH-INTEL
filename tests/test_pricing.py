from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.options.pricing import bs_price, greeks, implied_vol, year_fraction

S = np.array([90.0, 100.0, 110.0])
GRID = [(s, k, t, v) for s in (80.0, 100.0, 125.0) for k in (90.0, 100.0, 110.0)
        for t in (1 / 365 / 24 * 6, 1 / 365, 30 / 365, 1.0) for v in (0.15, 0.5, 1.2)]


def test_textbook_value_r0() -> None:
    assert bs_price(100, 100, 1.0, 0.2, "C") == pytest.approx(7.965567, abs=1e-5)


@pytest.mark.parametrize("s,k,t,v", GRID)
def test_put_call_parity_r0(s, k, t, v) -> None:
    assert bs_price(s, k, t, v, "C") - bs_price(s, k, t, v, "P") == pytest.approx(s - k, abs=1e-8)


def test_intrinsic_at_expiry_and_zero_vol() -> None:
    assert bs_price(110, 100, 0.0, 0.5, "C") == 10.0
    assert bs_price(90, 100, -1.0, 0.5, "P") == 10.0
    assert bs_price(90, 100, 0.5, 0.0, "C") == 0.0


@pytest.mark.parametrize("kind", ["C", "P"])
def test_greeks_match_finite_differences(kind) -> None:
    s, k, t, v, h = 100.0, 103.0, 20 / 365, 0.6, 1e-3
    g = greeks(s, k, t, v, kind)
    assert g["delta"] == pytest.approx((bs_price(s + h, k, t, v, kind) - bs_price(s - h, k, t, v, kind)) / (2 * h), rel=1e-5)
    assert g["gamma"] == pytest.approx((bs_price(s + h, k, t, v, kind) - 2 * bs_price(s, k, t, v, kind)
                                        + bs_price(s - h, k, t, v, kind)) / h ** 2, rel=1e-3)
    assert g["vega"] == pytest.approx((bs_price(s, k, t, v + h, kind) - bs_price(s, k, t, v - h, kind)) / (2 * h), rel=1e-5)
    dt_ = 1e-6
    assert g["theta_year"] == pytest.approx(-(bs_price(s, k, t + dt_, v, kind) - bs_price(s, k, t - dt_, v, kind)) / (2 * dt_), rel=1e-4)
    assert g["theta_day"] == pytest.approx(g["theta_year"] / 365)


def test_implied_vol_round_trip_on_grid() -> None:
    s, k, t, v = (np.array(x) for x in zip(*GRID))
    for kind in ("C", "P"):
        price = bs_price(s, k, t, v, kind)
        iv = implied_vol(price, s, k, t, np.full(len(s), kind))
        intrinsic = np.maximum(s - k, 0) if kind == "C" else np.maximum(k - s, 0)
        meaningful = price - intrinsic > 1e-6 * s  # with ~zero time value no volatility is recoverable
        assert not np.isnan(iv[meaningful]).any()
        np.testing.assert_allclose(iv[meaningful], v[meaningful], rtol=1e-4)


def test_object_dtype_kinds_price_puts_as_puts() -> None:
    """Regression: a pandas object column of 'C'/'P' once priced every put as a call."""
    kinds = pd.Series(["C", "P"], dtype=object).to_numpy()
    prices = bs_price(np.array([100.0, 100.0]), np.array([110.0, 110.0]), 0.1, 0.5, kinds)
    assert prices[1] > prices[0]  # OTM call cheaper than ITM put
    iv = implied_vol(prices, np.array([100.0, 100.0]), np.array([110.0, 110.0]), np.array([0.1, 0.1]), kinds)
    np.testing.assert_allclose(iv, [0.5, 0.5], rtol=1e-5)


def test_invalid_kind_raises() -> None:
    with pytest.raises(ValueError):
        bs_price(100, 100, 1, 0.2, "X")
    with pytest.raises(TypeError):
        bs_price(100, 100, 1, 0.2, np.array([True]))


def test_implied_vol_rejects_impossible_prices() -> None:
    assert np.isnan(implied_vol(5.0, 110, 100, 0.1, "C"))  # below intrinsic 10
    assert np.isnan(implied_vol(120.0, 110, 100, 0.1, "C"))  # above S
    assert np.isnan(implied_vol(3.0, 100, 100, 0.0, "C"))  # expired


def test_matches_delta_mark_price_live_sample() -> None:
    """Recorded from Delta's /v2/tickers on 2026-10-02 (C-BTC-99000-271126): mark 1075.77451774, spot 84812.7,
    mark_iv 0.38046638, delta 0.169421, timestamp 1790882235961172 us."""
    now = 1790882235961172 / 1e6
    expiry = dt.datetime(2026, 11, 27, 12, tzinfo=dt.timezone.utc).timestamp()
    t = year_fraction(expiry - now)
    assert bs_price(84812.7, 99000, t, 0.38046638, "C") == pytest.approx(1075.77451774, rel=1e-3)
    assert greeks(84812.7, 99000, t, 0.38046638, "C")["delta"] == pytest.approx(0.169421, abs=5e-4)
