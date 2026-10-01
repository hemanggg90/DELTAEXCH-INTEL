from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.feature_engine import AuxData, compute_features
from delta_intelligence.strategies import primitives as P
from delta_intelligence.strategies.context import build_strategy_frame

UTC = dt.timezone.utc


def hourly_iv(start, hours, seed=4):
    h = pd.date_range(start, periods=hours, freq="1h")
    v = 0.4 + 0.1 * np.sin(np.arange(hours) / 30) + np.random.default_rng(seed).normal(0, 0.02, hours)
    return pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_6_30h": v})


@pytest.fixture(scope="module")
def ctx():
    o = synthetic_ohlcv(dt.datetime(2026, 1, 5, tzinfo=UTC), 288 * 70, seed=21)
    hours = pd.date_range(o["timestamp"].iloc[0], o["timestamp"].iloc[-1], freq="1h")
    mk = lambda v: pd.DataFrame({"timestamp": hours, "open": v, "high": v, "low": v, "close": v, "volume": np.nan})  # noqa: E731
    aux = AuxData(index=o.assign(close=o["close"] * 0.9996), funding=mk(np.full(len(hours), 0.01)),
                  oi=mk(800 + np.cumsum(np.random.default_rng(2).normal(0, 4, len(hours)))))
    iv = hourly_iv(o["timestamp"].iloc[0] - pd.Timedelta(days=61), 24 * 135)
    events = pd.DataFrame({"timestamp_utc": pd.to_datetime(["2026-02-04T19:00Z", "2026-03-01T13:30Z"]),
                           "name": ["FOMC", "CPI"], "importance": ["high", "high"]})
    feats = compute_features(o, "5m", aux)
    return o, aux, iv, events, build_strategy_frame(o, feats, iv, events)


def test_swing_is_confirmed_n_bars_late() -> None:
    h = pd.Series([1, 2, 3, 9, 3, 2, 1, 1, 1], dtype=float)
    sw = P.confirmed_swings(h, h - 0.5, n=3)
    assert not sw["new_sh"].iloc[:6].any() and sw["new_sh"].iloc[6]  # peak at bar 3, known at bar 6
    assert sw["sh"].iloc[6] == 9 and sw["sh_idx"].iloc[6] == 3 and np.isnan(sw["sh"].iloc[5])


@pytest.mark.parametrize("cut", [288 * 20 + 7, 288 * 33 + 150, 288 * 61 + 3])
def test_whole_context_is_causal(ctx, cut) -> None:
    o, aux, iv, events, full = ctx
    cut_close = o["timestamp"].iloc[cut - 1] + pd.Timedelta("5min")
    aux_cut = AuxData(index=aux.index.iloc[:cut],
                      funding=aux.funding[aux.funding["timestamp"] + pd.Timedelta("1h") <= cut_close],
                      oi=aux.oi[aux.oi["timestamp"] + pd.Timedelta("1h") <= cut_close])
    iv_cut = iv[iv["available_at"] <= cut_close]
    o_cut = o.iloc[:cut].reset_index(drop=True)
    part = build_strategy_frame(o_cut, compute_features(o_cut, "5m", aux_cut), iv_cut, events)
    pd.testing.assert_frame_equal(full.iloc[:cut].reset_index(drop=True), part, check_dtype=False, atol=1e-9)


def test_killzones_are_dst_aware() -> None:
    summer = pd.Series(pd.date_range("2026-07-15T05:00Z", periods=24 * 12, freq="5min"))
    winter = pd.Series(pd.date_range("2026-01-15T05:00Z", periods=24 * 12, freq="5min"))
    lon_s = summer[P.in_window(summer, P.LONDON, dt.time(7), dt.time(10))]
    lon_w = winter[P.in_window(winter, P.LONDON, dt.time(7), dt.time(10))]
    assert lon_s.iloc[0].strftime("%H:%M") == "06:00" and lon_w.iloc[0].strftime("%H:%M") == "07:00"
    ny_s = summer[P.in_window(summer, P.NEW_YORK, dt.time(8), dt.time(11))]
    ny_w = winter[P.in_window(winter, P.NEW_YORK, dt.time(8), dt.time(11))]
    assert ny_s.iloc[0].strftime("%H:%M") == "12:00" and ny_w.iloc[0].strftime("%H:%M") == "13:00"


def test_asia_range_crosses_midnight_and_completes() -> None:
    ts = pd.Series(pd.date_range("2026-01-14T23:00Z", periods=12 * 12, freq="5min"))  # 18:00-06:00 NY (EST)
    h = pd.Series(np.arange(len(ts), dtype=float))
    r = P.window_range(ts, h, h - 1, P.NEW_YORK, dt.time(20), dt.time(0))
    inside_rows = r[r["inside"]]
    assert len(inside_rows) == 48  # 20:00-00:00 NY = 4 h
    after = r[r["complete"]]
    assert after["hi"].nunique() == 1 and after["hi"].iloc[0] == inside_rows["hi"].iloc[-1]


def test_displacement_fvg_and_bos() -> None:
    o = pd.Series([10, 10, 10, 10.0]); c = pd.Series([10, 10, 13, 10.0]); atr = pd.Series([1.0] * 4)
    assert list(P.displacement(o, c, atr)) == [0, 0, 1, 0]
    h = pd.Series([10, 11, 12, 13.0]); l = pd.Series([9, 10, 9.9, 12.0])
    g = P.fvg(h, l)
    assert g["fvg_bull"].tolist() == [False, False, False, True] and g["fvg_lo"].iloc[3] == 11 and g["fvg_hi"].iloc[3] == 12
    sw = pd.DataFrame({"sh": [np.nan, 20, 20, 20], "sl": [np.nan, 5, 5, 5]})
    b = P.structure_breaks(pd.Series([15, 19, 21, 22.0]), sw)
    assert b["bos_up"].tolist() == [False, False, True, False]


def test_order_block_is_last_opposite_candle_before_displacement() -> None:
    o = pd.Series([10, 11, 10.5, 10.2, 13.0]); c = pd.Series([11, 10.5, 10.2, 13.5, 13.2])
    h = pd.concat([o, c], axis=1).max(axis=1) + 0.1; l = pd.concat([o, c], axis=1).min(axis=1) - 0.1
    disp = pd.Series([0, 0, 0, 1, 0]); brk = pd.DataFrame({"bos_up": [False, False, False, True, False],
                                                           "bos_dn": [False] * 5})
    ob = P.order_blocks(o, c, h, l, disp, brk)
    assert ob["ob_dir"].iloc[3] == 1 and ob["ob_lo"].iloc[3] == pytest.approx(10.1)  # candle 2 (10.5 -> 10.2)


def test_iv_and_daily_context(ctx) -> None:
    *_, f = ctx
    assert f["iv_percentile"].dropna().between(0, 100).all() and f["iv_percentile"].notna().mean() > 0.5
    day_open = f[f["minutes_into_day"] == 0]
    assert day_open["d_close"].notna().sum() > 40  # completed days are visible from the next day on
    ev = f[f["hours_to_event"].notna()]
    assert ev["hours_to_event"].min() >= 0 and set(ev["event_name"]) <= {"FOMC", "CPI"}
