"""
Pluggable contract picking for research.

The default backtester path is unchanged: it uses `selector.choose_expiry` and `selector.build_long`. Option-selection
research supplies a `contract_picker(ctx) -> Pick` instead, so the SAME underlying signal can be expressed through
different option-selection policies. The picker only sees information available at the signal timestamp: the chain as
of then (real recorded quotes or the model), the underlying's features at the signal bar, and nothing later.

A picker may only return buying-only structures (the Structure constructors reject anything else).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.options.structures import Structure


@dataclass
class PickContext:
    direction: str  # LONG / SHORT / VOL
    policy: str  # LONG_OPTION / LONG_STRADDLE / LONG_STRANGLE
    underlying: str
    spot: float  # spot index at the signal bar close
    t_entry: pd.Timestamp
    hold_hours: float
    expiries: list  # listed expiries at t_entry (recorded chain when available, else the modelled listing)
    strikes_for: Callable[[pd.Timestamp], np.ndarray]
    abs_delta: Callable[[pd.Timestamp, str, float], float | None]  # (expiry, kind, strike)
    fill: Callable[[str, float, pd.Timestamp, bool], tuple | None]  # (kind, strike, expiry, buy) -> (px, mid, iv, src)
    greek: Callable[[str, float, pd.Timestamp], dict | None]  # real or model: iv, delta, gamma, theta_day, vega, ...
    liquidity: Callable[[str, float, pd.Timestamp], dict | None]  # REAL only: open_interest, volume, sizes, else None
    row: dict  # the frame row at the signal bar (rv, IV percentile, ...)
    contracts: int
    contract_value: float
    symbol_for: Callable[[str, float, pd.Timestamp], str]
    real_chain: bool  # True when the listing/quotes come from recorded data at t_entry


@dataclass
class Pick:
    expiry: pd.Timestamp | None = None
    structure: Structure | None = None
    reason: str = ""  # why nothing was picked (counted by the backtester); also the "why no trade" diagnostic
    detail: dict = field(default_factory=dict)  # what was chosen and what it was ranked on (for the audit trail)
