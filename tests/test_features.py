"""Feature engine: strict no-look-ahead on EVERY column, plus correctness of the 24x7 / day / as-of logic."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.alignment import asof_join, regular_grid
from delta_intelligence.features.feature_engine import (
    FEATURE_DEFINITIONS,
    AuxData,
    _rolling_pct_rank,
    _rolling_slope,
    compute_features,
)

UTC = dt.timezone.utc
START = dt.datetime(2026, 9, 1, tzinfo=UTC)  # a Tuesday, 00:00 UTC = exchange-day start
N = 288 * 10  # ten days of 5m bars


def make_aux(ohlcv: pd.DataFrame) -> AuxData:
    rng = np.random.default_rng(3)
    index = ohlcv.assign(close=ohlcv["close"] * (1 - 0.0004), volume=np.nan)
    hours = pd.date_range(ohlcv["timestamp"].iloc[0], ohlcv["timestamp"].iloc[-1], freq="1h")
    funding_vals = np.where(np.arange(len(hours)) % 50 < 40, 0.01, rng.normal(0.01, 0.01, len(hours)))
    funding = pd.DataFrame({"timestamp": hours, "open": funding_vals, "high": funding_vals, "low": funding_vals,
                            "close": funding_vals, "volume": np.nan}).drop(index=[5, 6, 77])  # real series has gaps
    oi_vals = 800 + np.cumsum(rng.normal(0, 3, len(hours)))
    oi = pd.DataFrame({"timestamp": hours, "open": oi_vals, "high": oi_vals, "low": oi_vals, "close": oi_vals,
                       "volume": np.nan})
    return AuxData(index=index, funding=funding, oi=oi)


@pytest.fixture(scope="module")
def data():
    ohlcv = synthetic_ohlcv(START, N)
    aux = make_aux(ohlcv)
    return ohlcv, aux, compute_features(ohlcv, "5m", aux)


def _available_by(aux_df: pd.DataFrame, cutoff_close: pd.Timestamp, step: str) -> pd.DataFrame:
    return aux_df[aux_df["timestamp"] + pd.Timedelta(step) <= cutoff_close]


@pytest.mark.parametrize("cut", [288 * 3 + 2,  # inside the opening range of day 4
                                 288 * 5,  # first bar of day 6 (exchange-day boundary, 05:30 IST)
                                 288 * 7 + 150,  # mid-day
                                 N - 1])
def test_no_lookahead_any_column(data, cut) -> None:
    ohlcv, aux, full = data
    truncated_in = ohlcv.iloc[:cut].reset_index(drop=True)
    cutoff_close = truncated_in["timestamp"].iloc[-1] + pd.Timedelta("5min")
    aux_cut = AuxData(index=aux.index.iloc[:cut],
                      funding=_available_by(aux.funding, cutoff_close, "1h"),
                      oi=_available_by(aux.oi, cutoff_close, "1h"))
    truncated = compute_features(truncated_in, "5m", aux_cut)
    pd.testing.assert_frame_equal(full.iloc[:cut].reset_index(drop=True), truncated, check_dtype=False,
                                  atol=1e-9, rtol=1e-9)


def test_no_lookahead_inside_opening_ranges(data) -> None:
    """Cut at every bar inside the opening range of several days. A leak of the final range value can't hide behind
    a lucky cut (e.g. a day whose high came in the first bar)."""
    ohlcv, aux, full = data
    for day in (2, 4, 6, 8):
        for k in range(1, 6):
            cut = 288 * day + k
            truncated = compute_features(ohlcv.iloc[:cut].reset_index(drop=True), "5m", AuxData(index=aux.index.iloc[:cut]))
            for col in ("opening_range_high", "opening_range_low", "or_complete", "vwap"):
                pd.testing.assert_series_equal(full[col].iloc[:cut].reset_index(drop=True), truncated[col],
                                               check_dtype=False, obj=f"{col} cut={cut}")


def test_every_column_documented(data) -> None:
    _, _, f = data
    assert set(f.columns) - {"timestamp"} == set(FEATURE_DEFINITIONS)


def test_naive_timestamps_rejected() -> None:
    df = synthetic_ohlcv(START, 50)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="tz-aware"):
        compute_features(df)


def test_opening_range_is_running_then_held(data) -> None:
    ohlcv, _, f = data
    day2 = slice(288, 288 * 2)
    hi, lo = ohlcv["high"].iloc[day2].reset_index(drop=True), ohlcv["low"].iloc[day2].reset_index(drop=True)
    orh = f["opening_range_high"].iloc[day2].reset_index(drop=True)
    for i in range(6):  # 00:00..00:25 bars form the 30-min range
        assert orh[i] == pytest.approx(hi[: i + 1].max())  # running, never the future final value
    assert (orh[6:] == hi[:6].max()).all()
    assert f["opening_range_low"].iloc[288 + 100] == pytest.approx(lo[:6].min())
    complete = f["or_complete"].iloc[day2].reset_index(drop=True)
    assert not complete[:5].any() and complete[5:].all()  # the 00:25 bar closes the range at 00:30


def test_partial_first_day_has_no_opening_range() -> None:
    df = synthetic_ohlcv(START + dt.timedelta(hours=3), 400)
    f = compute_features(df)
    first_day = f["timestamp"].dt.date == f["timestamp"].iloc[0].date()
    assert f.loc[first_day, "opening_range_high"].isna().all() and not f.loc[first_day, "or_complete"].any()


def test_vwap_resets_at_0000_utc_0530_ist(data) -> None:
    ohlcv, _, f = data
    i = 288 * 2  # 00:00 UTC bar
    tp = (ohlcv.loc[i, "high"] + ohlcv.loc[i, "low"] + ohlcv.loc[i, "close"]) / 3
    assert f.loc[i, "vwap"] == pytest.approx(tp)
    assert f.loc[i, "timestamp"].tz_convert("Asia/Kolkata").strftime("%H:%M") == "05:30"
    assert f.loc[i, "minutes_into_day"] == 0


def test_cpr_uses_previous_complete_day(data) -> None:
    ohlcv, _, f = data
    prev = ohlcv.iloc[288 * 3: 288 * 4]
    h, l, c = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
    row = f.iloc[288 * 4 + 10]
    assert row["cpr_pivot"] == pytest.approx((h + l + c) / 3)
    assert row["camarilla_r3"] == pytest.approx(c + (h - l) * 1.1 / 4)
    assert f["cpr_pivot"].iloc[:288].isna().all()  # day 1 has no previous day


def test_cpr_not_taken_from_two_days_ago_when_a_day_is_missing() -> None:
    df = synthetic_ohlcv(START, 288 * 4)
    df = df[(df.index < 288) | (df.index >= 288 * 2)].reset_index(drop=True)  # day 2 missing entirely
    f = compute_features(df)
    day3 = f["timestamp"].dt.date == (START + dt.timedelta(days=2)).date()
    assert f.loc[day3, "cpr_pivot"].isna().all()


def test_clock_features(data) -> None:
    _, _, f = data
    row = f[f["timestamp"] == pd.Timestamp("2026-09-02T07:50Z")].iloc[0]  # closes 07:55 UTC = 13:25 IST
    assert row["minutes_to_funding"] == pytest.approx(5)  # next funding 08:00 UTC
    assert row["hours_to_daily_expiry"] == pytest.approx(4 + 5 / 60)  # 12:00 UTC settlement
    assert row["session_asia"] and not row["session_europe"] and not row["session_us"]
    row = f[f["timestamp"] == pd.Timestamp("2026-09-02T14:30Z")].iloc[0]  # 20:00 IST
    assert row["session_europe"] and row["session_us"] and not row["session_asia"]
    sat = f[f["timestamp"] == pd.Timestamp("2026-09-05T10:00Z")].iloc[0]
    assert sat["is_weekend"]


def test_settlement_bar_rolls_expiry_to_next_day(data) -> None:
    _, _, f = data
    row = f[f["timestamp"] == pd.Timestamp("2026-09-02T11:55Z")].iloc[0]  # closes exactly at 12:00 settlement
    assert row["hours_to_daily_expiry"] == pytest.approx(24)


def test_asof_join_hides_unclosed_hourly_bar() -> None:
    base = pd.Series(pd.to_datetime(["2026-09-01T10:50Z", "2026-09-01T10:55Z"]))
    aux = pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-01T09:00Z", "2026-09-01T10:00Z"]), "v": [1.0, 2.0]})
    out = asof_join(base, "5m", aux, "1h", ["v"])
    assert list(out["v"]) == [1.0, 2.0]  # 10:00-11:00 bar visible only to the bar closing at 11:00


def test_regular_grid_forward_fills_gaps_only_from_past() -> None:
    df = pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-01T00:00Z", "2026-09-01T03:00Z"]), "x": [1.0, 2.0]})
    g = regular_grid(df, "1h", ["x"])
    assert list(g["x"]) == [1.0, 1.0, 1.0, 2.0]


def test_derivative_features_and_baseline_funding(data) -> None:
    _, _, f = data
    late = f.iloc[288 * 5:]
    assert late["has_funding_data"].all() and late["has_oi_data"].all() and late["has_index_data"].all()
    assert late["funding_z_7d"].notna().all()  # baseline-pinned stretches don't produce NaN
    assert late["basis_pct"].mean() == pytest.approx(0.04, rel=0.05)


def test_missing_aux_gives_nan_and_flags() -> None:
    f = compute_features(synthetic_ohlcv(START, 600))
    assert not f["has_funding_data"].any() and f["funding_rate"].isna().all() and f["basis_pct"].isna().all()


def test_realized_vol_is_annualised_for_24x7() -> None:
    df = synthetic_ohlcv(START, 2000)
    f = compute_features(df)
    r = np.log(df["close"]).diff()
    expect = r.iloc[-288:].std() * np.sqrt(365 * 288)
    assert f["realized_vol_1d"].iloc[-1] == pytest.approx(expect)


def test_helpers_match_reference_implementations() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(size=400)
    a[[3, 50]] = np.nan
    ref = pd.Series(a).rolling(100, min_periods=20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    np.testing.assert_allclose(_rolling_pct_rank(a, 100, 20), ref.to_numpy(), equal_nan=True)
    y = rng.normal(size=60).cumsum()
    slope = _rolling_slope(y, 20)
    assert slope[-1] == pytest.approx(np.polyfit(np.arange(20), y[-20:], 1)[0])


def test_indicator_sanity(data) -> None:
    _, _, f = data
    assert f["rsi_14"].dropna().between(0, 100).all()
    assert (f["atr_14"].dropna() >= 0).all()
    assert set(f["supertrend_direction"].dropna().unique()) <= {1.0, -1.0}
    assert f["atr_percentile_100"].dropna().between(0, 1).all()
