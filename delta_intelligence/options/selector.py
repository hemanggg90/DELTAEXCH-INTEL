"""
Contract selection: turn a directional view on the underlying into concrete, defined-risk option legs.

Shared by the backtester and the live engine, so both pick contracts the same way.

- **Expiry:** the nearest daily expiry (12:00 UTC = 17:30 IST) with at least `min_hours_to_expiry` remaining;
  otherwise the next day's (user rule, 2026-10-02: 6 h).
- **Strikes:** always from the expiry's ACTUAL listed strikes (BTC steps are typically 200, ETH 20).

| Policy | LONG view | SHORT view |
|---|---|---|
| LONG_OPTION | buy the call nearest the spot | buy the put nearest the spot |
| DEBIT_SPREAD | buy the ATM call, sell the call nearest the target (>= 1 strike above) | mirror with puts |
| CREDIT_SPREAD | sell the put nearest the spot at/below it, buy the put nearest the stop (>= 1 strike below) | sell the call at/above the spot, buy the call nearest the stop |
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from delta_intelligence.options.structures import Leg, Structure

SETTLE_HOUR_UTC = 12


def choose_expiry(at: pd.Timestamp, min_hours: float, listed: set[pd.Timestamp] | None = None,
                  max_days_ahead: int = 3) -> pd.Timestamp | None:
    """First 12:00 UTC expiry at least `min_hours` after `at` (and listed, if a listing is supplied)."""
    at = pd.Timestamp(at)
    day = at.normalize()
    for k in range(max_days_ahead + 1):
        exp = day + pd.Timedelta(days=k, hours=SETTLE_HOUR_UTC)
        if (exp - at) >= pd.Timedelta(hours=min_hours) and (listed is None or exp in listed):
            return exp
    return None


def nearest(strikes: np.ndarray, x: float) -> float:
    return float(strikes[np.argmin(np.abs(strikes - x))])


def _step_away(strikes: np.ndarray, base: float, x: float, up: bool) -> float:
    """Strike nearest x, but at least one listed strike above (up) / below (down) `base`."""
    side = strikes[strikes > base] if up else strikes[strikes < base]
    if not len(side):
        raise ValueError("no listed strike beyond the base strike")
    return nearest(side, x)


def build_structure(policy: str, direction: str, underlying: str, spot: float, expiry: pd.Timestamp,
                    strikes: np.ndarray, stop: float, target: float, contracts: int, contract_value: float,
                    symbol_for=None) -> Structure:
    """Legs for `policy`. `stop`/`target` are in INDEX terms (the options' underlying). Raises ValueError when the
    listed strikes can't express the structure."""
    strikes = np.sort(np.asarray(strikes, dtype="float64"))
    if not len(strikes):
        raise ValueError("no listed strikes")
    exp = expiry.to_pydatetime()
    long_view = direction == "LONG"

    def leg(kind, k, side):
        sym = symbol_for(kind, k, expiry) if symbol_for else ""
        return Leg(kind, k, exp, side, contracts, sym)

    if policy == "LONG_OPTION":
        k = nearest(strikes, spot)
        legs = [leg("C" if long_view else "P", k, 1)]
        name = "LONG_CALL" if long_view else "LONG_PUT"
    elif policy == "DEBIT_SPREAD":
        k_long = nearest(strikes, spot)
        k_short = _step_away(strikes, k_long, target, up=long_view)
        kind = "C" if long_view else "P"
        legs = [leg(kind, k_long, 1), leg(kind, k_short, -1)]
        name = "BULL_CALL_DEBIT" if long_view else "BEAR_PUT_DEBIT"
    elif policy == "CREDIT_SPREAD":
        if long_view:
            below = strikes[strikes <= spot]
            k_short = float(below.max()) if len(below) else nearest(strikes, spot)
            k_long = _step_away(strikes, k_short, stop, up=False)
            legs = [leg("P", k_long, 1), leg("P", k_short, -1)]
            name = "BULL_PUT_CREDIT"
        else:
            above = strikes[strikes >= spot]
            k_short = float(above.min()) if len(above) else nearest(strikes, spot)
            k_long = _step_away(strikes, k_short, stop, up=True)
            legs = [leg("C", k_long, 1), leg("C", k_short, -1)]
            name = "BEAR_CALL_CREDIT"
    else:
        raise ValueError(f"unknown structure policy {policy!r}")
    return Structure(name, underlying, legs, contract_value, "LONG" if long_view else "SHORT")


def option_symbol(kind: str, asset: str, strike: float, expiry: pd.Timestamp | dt.datetime) -> str:
    """Delta's symbol format, e.g. C-BTC-87000-021026 (verified)."""
    k = int(strike) if float(strike).is_integer() else strike
    return f"{kind}-{asset}-{k}-{pd.Timestamp(expiry):%d%m%y}"
