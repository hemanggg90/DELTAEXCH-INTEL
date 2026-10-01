"""
Loads everything the feature engine needs for one underlying:
- perp candles (the signal series; their quality gates trading),
- the spot index the options settle on,
- the hourly FUNDING and OI series.

Auxiliary series are best-effort. If one is unavailable, its features are NaN and the reason is recorded. The perp
candles are mandatory: without them `DataUnavailableError` propagates.

The combined quality status is the perp candles' status. If the spot index is not OK, the status is downgraded to
DEGRADED, because options are priced and settled on the index.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from delta_intelligence.config.watchlist import underlying_for
from delta_intelligence.data.data_manager import DataManager, DataUnavailableError
from delta_intelligence.features.feature_engine import HOURLY, AuxData
from delta_intelligence.utils.logging_utils import log_event


@dataclass
class FeatureInputs:
    symbol: str
    timeframe: str
    ohlcv: pd.DataFrame
    aux: AuxData
    quality_status: str
    metadata: dict = field(default_factory=dict)  # per series
    aux_errors: dict = field(default_factory=dict)


def load_feature_inputs(dm: DataManager, symbol: str, timeframe: str, start: dt.datetime,
                        end: dt.datetime | None = None) -> FeatureInputs:
    u = underlying_for(symbol)
    ohlcv, meta = dm.get_ohlcv(symbol, timeframe, start, end)
    metadata = {"perp": meta}
    errors: dict[str, str] = {}
    status = meta["quality_status"]

    def best_effort(name: str, fn):
        try:
            df, m = fn()
            metadata[name] = m
            return df, m
        except DataUnavailableError as exc:
            errors[name] = str(exc)
            log_event("features", f"{name} unavailable for {symbol}; dependent features will be NaN", level="WARNING",
                      reason=str(exc))
            return None, None

    index_df, index_meta = best_effort("index", lambda: dm.get_ohlcv(u.index_symbol, timeframe, start, end))
    if index_meta is None or index_meta["quality_status"] != "OK":
        status = "FAIL" if status == "FAIL" else "DEGRADED"
    funding_df, _ = best_effort("funding", lambda: dm.get_series("FUNDING", symbol, HOURLY,
                                                                 start - dt.timedelta(days=8), end))
    oi_df, _ = best_effort("oi", lambda: dm.get_series("OI", symbol, HOURLY, start - dt.timedelta(days=8), end))
    aux = AuxData(index=index_df, funding=funding_df, oi=oi_df)
    return FeatureInputs(symbol, timeframe, ohlcv, aux, status, metadata, errors)
