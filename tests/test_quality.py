from __future__ import annotations

import datetime as dt

import pandas as pd

from delta_intelligence.data.quality import clean_ohlcv, validate_ohlcv
from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv

UTC = dt.timezone.utc
START = dt.datetime(2026, 9, 1, tzinfo=UTC)


def frame(n: int = 600) -> tuple[pd.DataFrame, dt.datetime]:
    df = synthetic_ohlcv(START, n)
    now = df["timestamp"].iloc[-1].to_pydatetime() + dt.timedelta(minutes=7)  # last bar just closed
    return df, now


def test_clean_data_is_ok() -> None:
    df, now = frame()
    report = validate_ohlcv(df, "5m", now=now)
    assert report.status == "OK", report.issues


def test_naive_timestamps_fail() -> None:
    df, now = frame()
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    assert validate_ohlcv(df, "5m", now=now).status == "FAIL"


def test_off_grid_timestamps_fail() -> None:
    df, now = frame()
    df["timestamp"] = df["timestamp"] + pd.Timedelta(minutes=2)  # e.g. wrong offset
    report = validate_ohlcv(df, "5m", now=now)
    assert report.status == "FAIL" and report.n_off_grid == len(df)


def test_ohlc_violation_and_bad_price_fail() -> None:
    df, now = frame()
    df.loc[10, "high"] = df.loc[10, "low"] - 1
    assert validate_ohlcv(df, "5m", now=now).status == "FAIL"
    df, now = frame()
    df.loc[5, ["open", "high", "low", "close"]] = 0.0
    assert validate_ohlcv(df, "5m", now=now).status == "FAIL"


def test_negative_values_allowed_for_funding_series() -> None:
    df, now = frame()
    df[["open", "high", "low", "close"]] = -0.001
    assert validate_ohlcv(df, "5m", now=now, check_volume=False, allow_nonpositive=True).status == "OK"


def test_recent_gap_degrades_but_old_gap_is_informational() -> None:
    df, now = frame()
    recent = df.drop(index=[len(df) - 5])
    r = validate_ohlcv(recent, "5m", now=now)
    assert r.status == "DEGRADED" and r.n_missing_bars_recent == 1
    old = df.drop(index=[10, 11, 12])
    r = validate_ohlcv(old, "5m", now=now, recent_window_bars=288)
    assert r.status == "OK" and r.n_missing_bars_older == 3 and r.issues


def test_duplicates_and_staleness_degrade() -> None:
    df, now = frame()
    dup = pd.concat([df, df.tail(1)], ignore_index=True)
    assert validate_ohlcv(dup, "5m", now=now).status == "DEGRADED"
    stale = validate_ohlcv(df, "5m", now=now + dt.timedelta(minutes=30))
    assert stale.status == "DEGRADED" and stale.is_stale


def test_clean_drops_duplicates_and_forming_bar() -> None:
    df, now = frame(50)
    forming = df.tail(1).assign(timestamp=df["timestamp"].iloc[-1] + pd.Timedelta(minutes=5))
    messy = pd.concat([df, df.iloc[[3]], forming], ignore_index=True)
    out = clean_ohlcv(messy, "5m", now=now)
    assert len(out) == 50 and out["timestamp"].is_monotonic_increasing


def test_empty_frame_fails() -> None:
    assert validate_ohlcv(synthetic_ohlcv(START, 0), "5m").status == "FAIL"
