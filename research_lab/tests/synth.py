"""Deterministic synthetic frames for tests ONLY (never used by the research run)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.feature_engine import AuxData

UTC = dt.timezone.utc


def hourly(ts0: pd.Timestamp, ts1: pd.Timestamp, values) -> pd.DataFrame:
    h = pd.date_range(ts0.floor("h"), ts1, freq="1h")
    v = values(len(h)) if callable(values) else values
    return pd.DataFrame({"timestamp": h, "open": v, "high": v, "low": v, "close": v, "volume": np.nan})


def make_inputs(days: int = 40, seed: int = 21, funding: bool = True, oi: bool = True):
    o = synthetic_ohlcv(dt.datetime(2026, 1, 5, tzinfo=UTC), 288 * days, seed=seed)
    rng = np.random.default_rng(seed + 1)
    t0, t1 = o["timestamp"].iloc[0] - pd.Timedelta(days=8), o["timestamp"].iloc[-1]
    aux = AuxData(
        index=o.assign(close=o["close"] * 0.9996),
        funding=hourly(t0, t1, lambda n: rng.normal(0.01, 0.01, n)) if funding else None,
        oi=hourly(t0, t1, lambda n: 800 + np.cumsum(rng.normal(0, 8, n))) if oi else None)
    return o, aux


def iv_hourly(o: pd.DataFrame) -> pd.DataFrame:
    h = pd.date_range(o["timestamp"].iloc[0] - pd.Timedelta(days=61), periods=24 * 110, freq="1h")
    return pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"),
                         "atm_iv_6_30h": 0.4 + 0.15 * np.sin(np.arange(len(h)) / 50)})
