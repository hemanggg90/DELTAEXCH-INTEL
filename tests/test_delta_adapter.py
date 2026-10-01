from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from delta_intelligence.data_adapters.delta_adapter import PAGE_BARS, DeltaAdapter, candles_to_frame

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 1, 12, 7, 30, tzinfo=UTC)


class FakeCandleClient:
    """Serves 5m candles newest-first, including the forming bar, like Delta does."""

    def __init__(self, now: dt.datetime = NOW):
        self.now = now
        self.calls: list[tuple[str, str, int, int]] = []

    def get_candles(self, symbol, resolution, start, end):
        self.calls.append((symbol, resolution, start, end))
        step = 300
        last_open = int(self.now.timestamp()) // step * step  # the forming bar
        rows = []
        t = start - start % step
        while t <= min(end, last_open):
            if t >= start:
                rows.append({"time": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 7})
            t += step
        return list(reversed(rows))


def test_candles_to_frame_sorts_ascending_utc() -> None:
    df = candles_to_frame([{"time": 600, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
                           {"time": 300, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3}])
    assert list(df["timestamp"]) == [pd.Timestamp(300, unit="s", tz="UTC"), pd.Timestamp(600, unit="s", tz="UTC")]
    assert str(df["timestamp"].dt.tz) == "UTC" and pd.isna(df["volume"].iloc[1])


def test_forming_bar_is_dropped() -> None:
    adapter = DeltaAdapter(FakeCandleClient(), now=lambda: NOW)
    df = adapter.get_ohlcv("BTCUSD", "5m", NOW - dt.timedelta(hours=1), NOW)
    assert df["timestamp"].iloc[-1] == pd.Timestamp("2026-10-01T12:00Z")  # 12:05 bar is still forming
    assert df["timestamp"].is_monotonic_increasing and not df["timestamp"].duplicated().any()


def test_pagination_windows_are_contiguous_and_capped() -> None:
    client = FakeCandleClient()
    adapter = DeltaAdapter(client, now=lambda: NOW)
    start = NOW - dt.timedelta(days=10)  # 2880 bars -> 2 pages
    df = adapter.get_ohlcv("BTCUSD", "5m", start, NOW)
    assert len(client.calls) == 2
    for _, _, s, e in client.calls:
        assert (e - s) / 300 + 1 <= PAGE_BARS
    assert client.calls[1][2] == client.calls[0][3] + 300  # next window starts one bar later
    # first bar opening at/after 12:07:30 is 12:10; last closed bar is 12:00 ten days later: 2878 intervals -> 2879 bars
    assert len(df) == 2879
    assert (df["timestamp"].diff().dropna() == pd.Timedelta(minutes=5)).all()  # no gaps, no duplicates


def test_no_bars_after_end() -> None:
    adapter = DeltaAdapter(FakeCandleClient(), now=lambda: NOW)
    end = dt.datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
    df = adapter.get_ohlcv("BTCUSD", "5m", end - dt.timedelta(hours=2), end)
    assert df["timestamp"].max() <= pd.Timestamp(end)


def test_series_use_symbol_prefix() -> None:
    client = FakeCandleClient()
    DeltaAdapter(client, now=lambda: NOW).get_series("funding", "BTCUSD", "5m", NOW - dt.timedelta(hours=1), NOW)
    assert client.calls[0][0] == "FUNDING:BTCUSD"
    with pytest.raises(ValueError):
        DeltaAdapter(client).get_series("IV", "BTCUSD", "5m", NOW, NOW)
