"""
Contract selection for a BUYING-ONLY system. Shared by the backtester and the live engine.

**Direction → contract**
- LONG view → buy a call; SHORT view → buy a put.
- Strike: ATM by default (or 1 strike ITM if `moneyness="ITM1"`), accepted only if |delta| is within
  [delta_min, delta_max] (default 0.40-0.60). If neither ATM nor 1-ITM qualifies → no trade.
- Volatility view (event strategy) → long straddle (ATM call + put) or long strangle (1 strike OTM each side).

**Expiry:** the NEAREST listed expiry whose time to expiry is at least `dte_multiple` × the expected hold (default
2.5×), AND which leaves the whole expected hold before the expiry guard (default: exit 2 h before expiry). Expiry
times are 12:00 UTC (17:30 IST) for BTC/ETH and 16:00 UTC (21:30 IST) for XAUT; always shown in IST.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.options.structures import Structure, long_call, long_put, long_straddle, long_strangle

POLICIES = ("LONG_OPTION", "LONG_STRADDLE", "LONG_STRANGLE")


@dataclass(frozen=True)
class SelectorConfig:
    moneyness: str = "ATM"  # ATM / ITM1
    delta_min: float = 0.40
    delta_max: float = 0.60
    dte_multiple: float = 2.5
    expiry_guard_hours: float = 2.0
    strangle_steps: int = 1


def choose_expiry(now: pd.Timestamp, expected_hold_hours: float, expiries, cfg: SelectorConfig) -> pd.Timestamp | None:
    """Nearest expiry with TTE >= dte_multiple × hold and TTE − guard >= hold."""
    now = pd.Timestamp(now)
    for e in sorted(pd.Timestamp(x) for x in expiries):
        tte_h = (e - now).total_seconds() / 3600.0
        if tte_h >= cfg.dte_multiple * expected_hold_hours and tte_h - cfg.expiry_guard_hours >= expected_hold_hours:
            return e
    return None


def nearest(strikes: np.ndarray, x: float) -> float:
    return float(strikes[np.argmin(np.abs(strikes - x))])


def pick_strike(kind: str, spot: float, strikes: np.ndarray, abs_delta: Callable[[float], float | None],
                cfg: SelectorConfig) -> float | None:
    """ATM or 1-ITM strike whose |delta| is inside the configured band; None when neither qualifies."""
    strikes = np.sort(np.asarray(strikes, dtype="float64"))
    if not len(strikes):
        return None
    atm = nearest(strikes, spot)
    i = int(np.searchsorted(strikes, atm))
    itm = (strikes[i - 1] if i > 0 else None) if kind == "C" else (strikes[i + 1] if i + 1 < len(strikes) else None)
    order = [itm, atm] if cfg.moneyness == "ITM1" else [atm, itm]
    for k in order:
        if k is None:
            continue
        d = abs_delta(float(k))
        if d is not None and cfg.delta_min <= d <= cfg.delta_max:
            return float(k)
    return None


def build_long(policy: str, direction: str, underlying: str, spot: float, expiry: pd.Timestamp, strikes: np.ndarray,
               contracts: int, contract_value: float, abs_delta: Callable[[str, float], float | None],
               cfg: SelectorConfig, symbol_for: Callable[[str, float, pd.Timestamp], str] | None = None) -> Structure:
    """Raises ValueError when no listed strike satisfies the rules (→ no trade)."""
    strikes = np.sort(np.asarray(strikes, dtype="float64"))
    exp = expiry.to_pydatetime()
    sym = (lambda k, x: symbol_for(k, x, expiry)) if symbol_for else (lambda k, x: "")
    if policy == "LONG_OPTION":
        kind = "C" if direction == "LONG" else "P"
        k = pick_strike(kind, spot, strikes, lambda x: abs_delta(kind, x), cfg)
        if k is None:
            raise ValueError(f"no ATM/1-ITM {kind} strike with |delta| in [{cfg.delta_min}, {cfg.delta_max}]")
        builder = long_call if kind == "C" else long_put
        return builder(underlying, k, exp, contracts, contract_value, sym(kind, k))
    if policy == "LONG_STRADDLE":
        k = nearest(strikes, spot)
        return long_straddle(underlying, k, exp, contracts, contract_value, (sym("C", k), sym("P", k)))
    if policy == "LONG_STRANGLE":
        atm = nearest(strikes, spot)
        i = int(np.searchsorted(strikes, atm))
        s = cfg.strangle_steps
        if i - s < 0 or i + s >= len(strikes):
            raise ValueError("not enough listed strikes for a strangle")
        kp, kc = float(strikes[i - s]), float(strikes[i + s])
        return long_strangle(underlying, kp, kc, exp, contracts, contract_value, (sym("C", kc), sym("P", kp)))
    raise ValueError(f"unknown policy {policy!r} (buying-only policies: {POLICIES})")


def option_symbol(kind: str, asset: str, strike: float, expiry: pd.Timestamp | dt.datetime) -> str:
    """Delta's symbol format, e.g. C-BTC-87000-021026 (verified)."""
    k = int(strike) if float(strike).is_integer() else strike
    return f"{kind}-{asset}-{k}-{pd.Timestamp(expiry):%d%m%y}"
