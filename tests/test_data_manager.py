from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from delta_intelligence.data.data_manager import DataManager, DataUnavailableError
from delta_intelligence.data_adapters.csv_adapter import CSVAdapter
from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv

UTC = dt.timezone.utc
START = dt.datetime(2026, 9, 25, tzinfo=UTC)
FULL = synthetic_ohlcv(START, 2000)  # ~6.9 days of 5m bars


class FakeDelta:
    """Stands in for DeltaAdapter: serves FULL up to `now`, records calls, can be told to fail."""

    def __init__(self, now_ref: list):
        self.now_ref = now_ref
        self.calls: list[tuple] = []
        self.fail = False

    def _serve(self, start, end):
        if self.fail:
            raise ConnectionError("network down")
        last_closed = pd.Timestamp(self.now_ref[0]).floor("5min") - pd.Timedelta(minutes=5)
        df = FULL[(FULL["timestamp"] >= pd.Timestamp(start)) & (FULL["timestamp"] <= pd.Timestamp(end))]
        return df[df["timestamp"] <= last_closed].reset_index(drop=True)

    def get_ohlcv(self, symbol, timeframe, start, end):
        self.calls.append(("ohlcv", symbol, start, end))
        return self._serve(start, end)

    def get_series(self, kind, symbol, timeframe, start, end):
        self.calls.append((kind, symbol, start, end))
        return self._serve(start, end).assign(volume=float("nan"))


@pytest.fixture
def setup(tmp_path):
    now_ref = [FULL["timestamp"].iloc[1500].to_pydatetime() + dt.timedelta(minutes=6)]
    mono = [0.0]
    fake = FakeDelta(now_ref)
    dm = DataManager(fake, tmp_path / "cache", CSVAdapter(tmp_path / "csv"), now=lambda: now_ref[0],
                     monotonic=lambda: mono[0])
    return dm, fake, now_ref, mono, tmp_path


def test_first_fetch_writes_cache_and_second_is_free(setup) -> None:
    dm, fake, *_ = setup
    df, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "delta" and meta["quality_status"] == "OK" and len(df) == 1501
    assert dm.cache_path("BTCUSD", "5m").exists()
    df2, meta2 = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta2["source"] == "cache" and len(fake.calls) == 1 and df2.equals(df)


def test_tail_refresh_fetches_only_new_bars(setup) -> None:
    dm, fake, now_ref, mono, _ = setup
    dm.get_ohlcv("BTCUSD", "5m", START)
    now_ref[0] += dt.timedelta(minutes=15)
    mono[0] += 60
    df, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "cache+tail" and len(df) == 1504
    _, _, tail_start, _ = fake.calls[-1]
    assert tail_start >= FULL["timestamp"].iloc[1490].to_pydatetime()  # small tail, not a full refetch


def test_refetch_is_throttled(setup) -> None:
    dm, fake, now_ref, mono, _ = setup
    dm.get_ohlcv("BTCUSD", "5m", START)
    now_ref[0] += dt.timedelta(minutes=5)
    mono[0] += 5  # < MIN_REFETCH_INTERVAL_SEC
    _, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "cache" and len(fake.calls) == 1


def test_failed_refresh_serves_cache_and_says_why(setup) -> None:
    dm, fake, now_ref, mono, _ = setup
    dm.get_ohlcv("BTCUSD", "5m", START)
    fake.fail = True
    now_ref[0] += dt.timedelta(hours=1)
    mono[0] += 60
    _, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "cache" and meta["quality_status"] == "DEGRADED"
    assert any("network down" in i for i in meta["quality_report"]["issues"])


def test_no_data_raises_with_reason(setup) -> None:
    dm, fake, *_ = setup
    fake.fail = True
    with pytest.raises(DataUnavailableError, match="network down"):
        dm.get_ohlcv("BTCUSD", "5m", START)


def test_csv_takes_precedence_and_handles_ist(setup, monkeypatch) -> None:
    dm, fake, now_ref, _, tmp_path = setup
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    ist = FULL.head(1400).copy()
    ist["timestamp"] = ist["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)  # naive IST file
    ist.to_csv(csv_dir / "BTCUSD_5m.csv", index=False)
    dm.csv = CSVAdapter(csv_dir, naive_tz="Asia/Kolkata")
    df, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "csv" and fake.calls == []
    assert df["timestamp"].iloc[0] == FULL["timestamp"].iloc[0]  # converted back to the right UTC instant


def test_corrupt_cache_is_rebuilt(setup) -> None:
    dm, fake, *_ = setup
    path = dm.cache_path("BTCUSD", "5m")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")
    _, meta = dm.get_ohlcv("BTCUSD", "5m", START)
    assert meta["source"] == "delta" and len(fake.calls) == 1


def test_series_cached_separately_and_quality_ignores_volume(setup) -> None:
    dm, fake, *_ = setup
    df, meta = dm.get_series("FUNDING", "BTCUSD", "5m", START)
    assert meta["instrument"] == "FUNDING:BTCUSD" and meta["quality_status"] == "OK"
    assert dm.cache_path("FUNDING:BTCUSD", "5m").name == "FUNDING_BTCUSD_5m.parquet"
    assert fake.calls[0][0] == "FUNDING"


def test_extending_lookback_refetches(setup) -> None:
    dm, fake, *_ = setup
    dm.get_ohlcv("BTCUSD", "5m", START + dt.timedelta(days=3))
    dm.get_ohlcv("BTCUSD", "5m", START)  # earlier start than the cache covers
    assert len(fake.calls) == 2
