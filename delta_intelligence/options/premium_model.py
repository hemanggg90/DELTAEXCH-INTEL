"""
Premium model: translate a strategy's UNDERLYING stop/target into PREMIUM levels, and gate trades whose expected move
can't pay for the option.

**Translation:** full Black-Scholes repricing (r = 0; Delta's convention, verified) with the live IV per leg.
- premium at the underlying TARGET: valued after the expected hold, so the theta of the hold is included;
- premium at the underlying STOP: valued immediately (no time decay; the less favourable assumption for a stop).

The delta-gamma-theta approximation dP ≈ Δ·dS + ½Γ·dS² + Θ·dt is also reported. It is what traders reason with, and
it diverges from the full repricing for big moves.

**Premium stop:** exit when the structure's bid value falls `premium_stop_pct` (default 35%) below its entry cost.

**Breakeven gate:** skip unless
    expected_move >= (extrinsic premium + exit spread + round-trip fees, per underlying unit) × (1 + margin)
- expected move = |target − spot| (or the strategy's expected absolute move for straddles/strangles);
- extrinsic premium is used because an ITM option's intrinsic value is recovered when it is sold.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from delta_intelligence.options.pricing import bs_price, greeks, year_fraction
from delta_intelligence.options.structures import Structure


@dataclass
class PremiumPlan:
    entry_cost_per_unit: float  # sum of leg ask prices (USD per 1 underlying unit per leg)
    premium_stop: float  # structure value (per unit) that triggers the premium stop
    value_at_target: float | None
    value_at_underlying_stop: float | None
    approx_change_at_target: float | None  # delta-gamma-theta estimate
    expected_move: float
    required_move: float
    passes_breakeven: bool
    greeks: dict = field(default_factory=dict)


def _value(st: Structure, spot: float, at: dt.datetime, ivs: list[float]) -> float:
    T = max(0.0, (st.expiry - at).total_seconds())
    return float(sum(bs_price(spot, leg.strike, year_fraction(T), iv, leg.kind) for leg, iv in zip(st.legs, ivs)))


def plan_premium(st: Structure, spot: float, now: dt.datetime, ivs: list[float], ask_prices: list[float],
                 exit_half_spreads: list[float], fees_per_unit: float, underlying_stop: float | None,
                 underlying_target: float | None, expected_hold_hours: float, premium_stop_pct: float = 35.0,
                 breakeven_margin: float = 0.25, expected_abs_move: float | None = None) -> PremiumPlan:
    entry = float(sum(ask_prices))
    later = now + dt.timedelta(hours=expected_hold_hours)
    v_target = _value(st, underlying_target, later, ivs) if underlying_target is not None else None
    v_stop = _value(st, underlying_stop, now, ivs) if underlying_stop is not None else None
    T = year_fraction(max(0.0, (st.expiry - now).total_seconds()))
    g = {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "vega": 0.0}
    for leg, iv in zip(st.legs, ivs):
        gl = greeks(spot, leg.strike, T, iv, leg.kind)
        for k in g:
            g[k] += float(gl[k])
    approx = None
    if underlying_target is not None:
        ds = underlying_target - spot
        approx = g["delta"] * ds + 0.5 * g["gamma"] * ds * ds + g["theta_day"] * expected_hold_hours / 24.0
    intrinsic = sum(max(spot - leg.strike, 0.0) if leg.kind == "C" else max(leg.strike - spot, 0.0) for leg in st.legs)
    extrinsic = max(entry - intrinsic, 0.0)
    required = (extrinsic + sum(exit_half_spreads) + fees_per_unit) * (1.0 + breakeven_margin)
    if expected_abs_move is not None:
        move = abs(expected_abs_move)
    elif underlying_target is not None:
        move = abs(underlying_target - spot)
    else:
        move = 0.0
    return PremiumPlan(entry_cost_per_unit=entry, premium_stop=entry * (1 - premium_stop_pct / 100.0),
                       value_at_target=v_target, value_at_underlying_stop=v_stop, approx_change_at_target=approx,
                       expected_move=move, required_move=required, passes_breakeven=move >= required, greeks=g)
