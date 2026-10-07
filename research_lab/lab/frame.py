"""Builds the research frame for a signal timeframe (5m native, 15m by causal resampling of the 5m bars).

Everything comes from the project's `compute_features` (reused), plus the few columns the project lacks (ADX, percentile
ranks). Only COMPLETE higher-timeframe bars are used, so a 15m bar is never built from a partly formed candle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.features.feature_engine import AuxData, compute_features
from delta_intelligence.regimes.regime_engine import classify_frame
from delta_intelligence.strategies.base import research_frame
from delta_intelligence.strategies.context import iv_context
from delta_intelligence.utils.timeutil import timeframe_seconds
from lab.indicators import pct_rank, wilder_adx

BASE_TF = "5m"
SUPPORTED_TFS = ("5m", "15m")


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """5m bars -> `timeframe` bars. A bucket is kept only if ALL its 5m bars are present (no partial candles)."""
    if timeframe == BASE_TF:
        return df.sort_values("timestamp").reset_index(drop=True)
    step = timeframe_seconds(timeframe)
    per = step // timeframe_seconds(BASE_TF)
    t = pd.to_datetime(df["timestamp"], utc=True)
    d = df.assign(timestamp=t, _b=t.dt.floor(f"{step}s")).sort_values("timestamp")
    has_vol = "volume" in d
    agg = {"open": ("open", "first"), "high": ("high", "max"), "low": ("low", "min"), "close": ("close", "last"),
           "n": ("open", "size")}
    if has_vol:
        agg["volume"] = ("volume", "sum")
    out = d.groupby("_b").agg(**agg)
    out = out[out["n"] == per].drop(columns="n").reset_index().rename(columns={"_b": "timestamp"})
    return out.reset_index(drop=True)


def build_lab_frame(ohlcv5: pd.DataFrame, aux5: AuxData, timeframe: str = BASE_TF, atm_hourly: pd.DataFrame | None = None,
                    with_regime: bool = True) -> pd.DataFrame:
    """One row per closed `timeframe` bar: OHLCV + project features + lab columns (adx, percentile ranks, regime)."""
    if timeframe not in SUPPORTED_TFS:
        raise ValueError(f"timeframe must be one of {SUPPORTED_TFS}")
    ohlcv = resample_ohlcv(ohlcv5, timeframe)
    aux = aux5
    if timeframe != BASE_TF:
        idx = resample_ohlcv(aux5.index, timeframe) if aux5.index is not None and len(aux5.index) else aux5.index
        aux = AuxData(index=idx, funding=aux5.funding, oi=aux5.oi, index_timeframe=timeframe,
                      series_timeframe=aux5.series_timeframe)
    feats = compute_features(ohlcv, timeframe, aux)
    f = research_frame(ohlcv, feats)
    f = pd.concat([f, wilder_adx(f["high"], f["low"], f["close"], 14)], axis=1)
    f["bbw"] = (f["bb_upper_20"] - f["bb_lower_20"]) / f["bb_mid_20"]
    f["bbw_pct"] = pct_rank(f["bbw"])
    f["atr_pct"] = pct_rank(f["atr_14"])
    f["rv_pct"] = pct_rank(f["realized_vol_20"])
    ivc = iv_context(f["timestamp"], atm_hourly)  # as-of ATM IV percentile (0..100); NaN when no IV history
    f["atm_iv"], f["iv_percentile"] = ivc["atm_iv"].to_numpy(), ivc["iv_percentile"].to_numpy()
    f["regime"] = classify_frame(feats)[0] if with_regime else "UNKNOWN"
    return f


def to_base_timestamp(ts: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    """Open time of the LAST 5m bar inside a `timeframe` bar. The backtester enters at the close of a 5m bar, which for
    that bar equals the close of the signal bar, so a 15m signal at open T enters at T+15m exactly as it should."""
    return ts + pd.Timedelta(seconds=timeframe_seconds(timeframe) - timeframe_seconds(BASE_TF))


def map_setups(setups: list, timeframe: str) -> list:
    """Re-time setups made on a higher timeframe onto the 5m frame the backtester runs on."""
    if timeframe == BASE_TF:
        return setups
    from dataclasses import replace

    return [replace(s, timestamp=to_base_timestamp(pd.Timestamp(s.timestamp), timeframe)) for s in setups]


def coverage(f: pd.DataFrame) -> dict:
    """Data coverage facts for the report."""
    def frac(c):
        return float(np.isfinite(f[c]).mean()) if c in f else 0.0

    return {"bars": int(len(f)), "start": str(f["timestamp"].iloc[0]) if len(f) else None,
            "end": str(f["timestamp"].iloc[-1]) if len(f) else None, "funding_z": frac("funding_z_7d"),
            "oi_z": frac("oi_change_z_7d"), "index_close": frac("index_close"), "iv_percentile": frac("iv_percentile")}
