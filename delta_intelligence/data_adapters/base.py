"""
Data adapter interface.

Every market-data source (user CSV/Parquet files, Delta, and the test-only synthetic generator) implements this
interface, so the research engine never depends on a vendor.

Contract:
- columns `OHLCV_COLUMNS`
- `timestamp` is the **bar open time** as a timezone-aware UTC pandas Timestamp
- rows sorted ascending, no duplicates
- **no row with timestamp > end** (no look-ahead)
- only **closed** bars
"""
from __future__ import annotations

import abc
import datetime as dt

import pandas as pd

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def empty_ohlcv() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS})
    df["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")
    return df


class DataAdapter(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def get_ohlcv(self, instrument: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        """Closed bars with start <= timestamp <= end (aware UTC datetimes)."""
        raise NotImplementedError

    def is_available(self) -> bool:
        return True
