"""
Option-chain analytics: per-expiry summary (ATM strike, ATM IV, put/call ratios, OI) and the IV percentile of the
current ATM IV against stored history.

IV-percentile history comes from:
1. the hourly ATM IV series inferred from real Delta trades (`options/iv_history.py`), and
2. recorded chain snapshots, once the chain recorder (P5) has accumulated them.

Only values available AT OR BEFORE `now` are used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.options.chain import OptionChain


@dataclass
class ExpirySummary:
    underlying: str
    expiry: pd.Timestamp
    atm_strike: float
    atm_iv: float | None
    pcr_oi: float | None
    pcr_volume: float | None
    total_oi: float
    n_strikes: int


def summarize_chain(chain: OptionChain, underlying: str, spot: float) -> list[ExpirySummary]:
    out = []
    for e in chain.expiries(underlying):
        qs = [q for q in chain.by_symbol.values() if q.underlying == underlying and q.expiry == e]
        strikes = np.array(sorted({q.strike for q in qs}))
        atm = float(strikes[np.argmin(np.abs(strikes - spot))])
        call_oi = sum(q.open_interest or 0 for q in qs if q.kind == "C")
        put_oi = sum(q.open_interest or 0 for q in qs if q.kind == "P")
        call_v = sum(q.volume or 0 for q in qs if q.kind == "C")
        put_v = sum(q.volume or 0 for q in qs if q.kind == "P")
        out.append(ExpirySummary(underlying, e, atm, chain.atm_iv(underlying, e, spot),
                                 put_oi / call_oi if call_oi > 0 else None, put_v / call_v if call_v > 0 else None,
                                 call_oi + put_oi, len(strikes)))
    return out


def iv_percentile(current_iv: float, history: pd.Series, now: pd.Timestamp, lookback_days: int = 60,
                  min_obs: int = 100) -> float | None:
    """Percentile (0-100) of `current_iv` within `history` (indexed by AVAILABILITY time) over the lookback. None when
    the history is too short to be meaningful."""
    if current_iv is None or not np.isfinite(current_iv):
        return None
    if history is None or not len(history) or not isinstance(history.index, pd.DatetimeIndex):
        return None
    h = history.dropna()
    h = h[(h.index <= now) & (h.index > now - pd.Timedelta(days=lookback_days))]
    if len(h) < min_obs:
        return None
    return float((h < current_iv).mean() * 100 + (h == current_iv).mean() * 50)
