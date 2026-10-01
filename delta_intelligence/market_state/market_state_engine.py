"""
Market state: the latest fully formed feature row, plus the data-quality status that gates every downstream decision.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd

from delta_intelligence.regimes.regime_engine import classify_regime, regime_confidence

# EMA(200) and the 24 h realised vol need this much history before the state is meaningful.
MIN_WARMUP_BARS = 300


@dataclass
class MarketState:
    instrument: str
    timestamp: dt.datetime  # OPEN time of the latest closed bar (UTC)
    features: dict
    data_quality_status: str
    regime: str = ""
    regime_probabilities: dict[str, float] = field(default_factory=dict)

    @property
    def regime_confidence(self) -> float:
        return regime_confidence(self.regime_probabilities)

    def get(self, key: str, default=None):
        val = self.features.get(key, default)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return val


def build_current_state(instrument: str, feature_df: pd.DataFrame, data_quality_status: str = "OK",
                        min_warmup_bars: int = MIN_WARMUP_BARS) -> MarketState | None:
    """Latest usable state, or None when there is not enough warm-up history."""
    if len(feature_df) < min_warmup_bars:
        return None
    row = feature_df.iloc[-1]
    features = row.drop(labels=["timestamp"]).to_dict()
    label, probs = classify_regime(features)
    return MarketState(instrument=instrument, timestamp=row["timestamp"].to_pydatetime(), features=features,
                       data_quality_status=data_quality_status, regime=label, regime_probabilities=probs)
