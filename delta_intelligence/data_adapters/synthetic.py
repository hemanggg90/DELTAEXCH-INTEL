"""
Synthetic 24x7 OHLCV. **For tests only.** Nothing in the runtime path imports this module; a test enforces that.

It produces a regime-switching random walk on a continuous UTC grid, so research code can be exercised without the
network.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from delta_intelligence.data_adapters.base import OHLCV_COLUMNS
from delta_intelligence.utils.timeutil import ensure_utc, timeframe_seconds


def synthetic_ohlcv(start: dt.datetime, n_bars: int, timeframe: str = "5m", seed: int = 7,
                    base_price: float = 60_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = timeframe_seconds(timeframe)
    ts = pd.date_range(ensure_utc(start), periods=n_bars, freq=f"{step}s")
    seg = np.repeat(rng.integers(0, 3, size=n_bars // 200 + 1), 200)[:n_bars]  # 0 trend, 1 range, 2 high-vol
    drift = np.where(seg == 0, 0.00015, 0.0)
    vol = np.where(seg == 2, 0.004, 0.0015)
    rets = rng.normal(drift, vol)
    close = base_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[base_price], close[:-1]])[:n_bars]
    wick = np.abs(rng.normal(0, vol, n_bars)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    volume = rng.lognormal(10, 0.5, n_bars)
    df = pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
    return df[OHLCV_COLUMNS]
