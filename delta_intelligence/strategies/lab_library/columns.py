"""Extra frame columns the S1-S10 strategies read. Identical definitions to research_lab/lab/frame.py (all causal)."""
from __future__ import annotations

import pandas as pd

from delta_intelligence.strategies.lab_library.indicators import pct_rank, wilder_adx

LAB_COLUMNS = ("adx_14", "plus_di_14", "minus_di_14", "bbw", "bbw_pct", "atr_pct", "rv_pct")


def add_lab_columns(f: pd.DataFrame) -> pd.DataFrame:
    """Return `f` with ADX/+DI/-DI, Bollinger-width, ATR and realised-vol percentile ranks added. `f` needs OHLC plus the
    project's `bb_upper_20/bb_lower_20/bb_mid_20`, `atr_14` and `realized_vol_20` features."""
    out = f.copy()
    for c, v in wilder_adx(out["high"], out["low"], out["close"], 14).items():
        out[c] = v
    out["bbw"] = (out["bb_upper_20"] - out["bb_lower_20"]) / out["bb_mid_20"]
    out["bbw_pct"] = pct_rank(out["bbw"])
    out["atr_pct"] = pct_rank(out["atr_14"])
    out["rv_pct"] = pct_rank(out["realized_vol_20"])
    return out
