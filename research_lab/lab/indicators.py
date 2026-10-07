"""Causal indicators the project does not have yet. Every value at row i uses rows <= i only.

Existing project columns (EMA, ATR14, Bollinger, VWAP, relative volume, supertrend, realized vol, funding/OI z-scores)
are reused from `compute_features`. This module adds only: ADX/+DI/-DI, ATR-normalised distance, configurable
Donchian channels, and trailing percentile ranks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.features.feature_engine import _rolling_pct_rank  # existing helper, reused


def wilder_adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    """Wilder's ADX, +DI and -DI. Smoothing is an EWM with alpha 1/n (Wilder's), so it is causal. The ADX needs about
    2n bars to warm up; earlier rows are NaN."""
    up, dn = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    a = 1.0 / n
    atr = tr.ewm(alpha=a, adjust=False, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr.replace(0, np.nan)
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=a, adjust=False, min_periods=n).mean()
    adx[: 2 * n - 1] = np.nan
    return pd.DataFrame({"adx_14": adx, "plus_di_14": pdi, "minus_di_14": mdi}, index=high.index)


def atr_distance(close: pd.Series, fair: pd.Series, atr: pd.Series) -> pd.Series:
    """(close - fair) / ATR: the signed deviation from a fair-value measure in ATR units."""
    return (close - fair) / atr.replace(0, np.nan)


def donchian(high: pd.Series, low: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """Highest high / lowest low of the PREVIOUS n bars (the current bar is excluded)."""
    return high.shift(1).rolling(n, min_periods=n).max(), low.shift(1).rolling(n, min_periods=n).min()


def pct_rank(s: pd.Series, window: int = 100, min_periods: int = 50) -> pd.Series:
    """Percentile rank (0..1) of each value within its own trailing window (the value itself included)."""
    return pd.Series(_rolling_pct_rank(s.to_numpy(float), window, min_periods), index=s.index)


def rising_edge(cond: pd.Series) -> pd.Series:
    """True only on the first bar where `cond` becomes true (the previous bar was false or unknown)."""
    c = cond.fillna(False).astype(bool)
    return c & ~c.shift(1, fill_value=False)
