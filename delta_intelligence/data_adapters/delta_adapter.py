"""
Delta Exchange candles: OHLCV plus the MARK, FUNDING and OI series.

Facts verified on 2026-10-01 (docs/DELTA_API_NOTES.md section 6):
- `GET /v2/history/candles` is public. `start`/`end` are in seconds. Each item is `{time (bar open, s), open, high,
  low, close, volume}`.
- Results come **newest first** and **include the still-forming bar**.
- The docs cap a response at 2000 candles (a live call returned 4000), so we page in windows of `PAGE_BARS` (≤2000).
- Symbol prefixes `MARK:`, `FUNDING:` and `OI:` return those series as OHLC, with `volume` null.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.data_adapters.base import OHLCV_COLUMNS, DataAdapter, empty_ohlcv
from delta_intelligence.utils.timeutil import ensure_utc, last_closed_bar_start, now_utc, timeframe_seconds

PAGE_BARS = 2000
SERIES_PREFIXES = ("MARK", "FUNDING", "OI")


def candles_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Raw Delta candles to an ascending OHLCV frame with tz-aware UTC bar-open timestamps."""
    if not rows:
        return empty_ohlcv()
    df = pd.DataFrame(rows)
    if "time" not in df.columns:
        raise ValueError("Delta candle rows have no 'time' field")
    out = pd.DataFrame({
        "timestamp": pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True),
        **{c: pd.to_numeric(df.get(c), errors="coerce") for c in ("open", "high", "low", "close", "volume")},
    })
    out = out.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return out[OHLCV_COLUMNS]


class DeltaAdapter(DataAdapter):
    name = "delta"

    def __init__(self, client: DeltaClient, now: callable = now_utc):
        self.client = client
        self.now = now

    def is_available(self) -> bool:
        return True  # candles are public: no credentials needed

    def _fetch_range(self, symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        step = timeframe_seconds(timeframe)
        # Align to the bar grid (round start UP to the next bar open). Unaligned windows would leave a bar between
        # one page's end and the next page's start.
        start_s = -(-int(ensure_utc(start).timestamp()) // step) * step
        end_s = int(ensure_utc(end).timestamp())
        frames: list[pd.DataFrame] = []
        window_start = start_s
        while window_start <= end_s:
            window_end = min(end_s, window_start + step * (PAGE_BARS - 1))
            frames.append(candles_to_frame(self.client.get_candles(symbol, timeframe, window_start, window_end)))
            window_start = window_end + step
        if not frames:
            return empty_ohlcv()
        df = pd.concat([f for f in frames if len(f)] or [empty_ohlcv()], ignore_index=True)
        return df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    def get_ohlcv(self, instrument: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        """Closed bars in [start, end]. The forming bar is dropped, so a signal never repaints."""
        now = ensure_utc(self.now())
        df = self._fetch_range(instrument, timeframe, start, min(ensure_utc(end), now))
        if len(df) == 0:
            return df
        last_closed = pd.Timestamp(last_closed_bar_start(now, timeframe))
        df = df[(df["timestamp"] <= last_closed) & (df["timestamp"] >= ensure_utc(start))
                & (df["timestamp"] <= ensure_utc(end))]
        return df.reset_index(drop=True)

    def get_series(self, kind: str, instrument: str, timeframe: str, start: dt.datetime,
                   end: dt.datetime) -> pd.DataFrame:
        """MARK, FUNDING or OI series as OHLC (volume is NaN). FUNDING has gaps; forward-fill only as-of."""
        kind = kind.upper()
        if kind not in SERIES_PREFIXES:
            raise ValueError(f"kind must be one of {SERIES_PREFIXES}")
        return self.get_ohlcv(f"{kind}:{instrument}", timeframe, start, end)
