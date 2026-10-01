"""
User-supplied CSV/Parquet candles, the first source in the data order.

Layout: `data_cache/csv/<SYMBOL>_<TIMEFRAME>.csv` (or `.parquet`), with columns
`timestamp,open,high,low,close,volume`. `timestamp` is the bar OPEN time.

Timezones:
- timestamps with an offset (e.g. `2026-10-01T05:30:00+05:30`) are converted to UTC;
- naive timestamps are read as `CSV_TIMEZONE`, which defaults to UTC. Set `CSV_TIMEZONE=Asia/Kolkata` if your file
  is in IST.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd

from delta_intelligence.data_adapters.base import OHLCV_COLUMNS, DataAdapter, empty_ohlcv
from delta_intelligence.utils.timeutil import ensure_utc


class CSVAdapter(DataAdapter):
    name = "csv"

    def __init__(self, directory: Path, naive_tz: str | None = None):
        self.directory = Path(directory)
        self.naive_tz = naive_tz or os.getenv("CSV_TIMEZONE", "UTC")

    def path_for(self, instrument: str, timeframe: str) -> Path | None:
        for ext in ("parquet", "csv"):
            p = self.directory / f"{instrument}_{timeframe}.{ext}"
            if p.exists():
                return p
        return None

    def is_available(self) -> bool:
        return self.directory.is_dir() and (any(self.directory.glob("*.csv")) or any(self.directory.glob("*.parquet")))

    def get_ohlcv(self, instrument: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        path = self.path_for(instrument, timeframe)
        if path is None:
            return empty_ohlcv()
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name} is missing columns {missing}")
        ts = pd.to_datetime(df["timestamp"], utc=False, format="mixed")
        ts = ts.dt.tz_localize(self.naive_tz) if ts.dt.tz is None else ts
        df = df.assign(timestamp=ts.dt.tz_convert("UTC"))
        df = df[(df["timestamp"] >= ensure_utc(start)) & (df["timestamp"] <= ensure_utc(end))]
        return df.sort_values("timestamp").reset_index(drop=True)[OHLCV_COLUMNS]
