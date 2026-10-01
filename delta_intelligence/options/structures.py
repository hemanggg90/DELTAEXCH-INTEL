"""
Defined-risk option structures: long call, long put, and debit or credit vertical spreads. Naked short options are
impossible to construct.

Units:
- Prices and premiums are in USD per 1 unit of the underlying, as Delta quotes them.
- A leg's `contracts` × `contract_value` (BTC: 0.001) gives units of the underlying, so the USD value of a leg is
  premium × contracts × contract_value.

Every structure knows its exact payoff at expiry, net premium, max loss and max profit (both finite, or the
structure is rejected), and breakevens.

Fees follow Delta's option schedule (product fields, verified 2026-10-02): commission_rate × notional, capped at
premium_cap_rate × premium value, per leg per fill. GST is charged on the fee (18%, UNVERIFIED). Whether "notional"
means spot × size is UNVERIFIED; see API notes section 12.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from delta_intelligence.options.pricing import bs_price, greeks, year_fraction


class UndefinedRiskError(ValueError):
    """The structure has an uncovered short leg (unbounded or undefined loss). Refused by design."""


@dataclass(frozen=True)
class Leg:
    kind: str  # "C" or "P"
    strike: float
    expiry: dt.datetime  # 12:00 UTC settlement, tz-aware
    side: int  # +1 long (bought), -1 short (sold)
    contracts: int
    symbol: str = ""

    def __post_init__(self):
        if self.kind not in ("C", "P"):
            raise ValueError("kind must be 'C' or 'P'")
        if self.side not in (1, -1):
            raise ValueError("side must be +1 or -1")
        if self.contracts <= 0:
            raise ValueError("contracts must be positive")
        if self.expiry.tzinfo is None:
            raise ValueError("expiry must be timezone-aware")

    def payoff(self, settle: float | np.ndarray) -> np.ndarray:
        """Intrinsic value per 1 unit of the underlying at settlement, signed by side."""
        s = np.asarray(settle, dtype="float64")
        value = np.maximum(s - self.strike, 0.0) if self.kind == "C" else np.maximum(self.strike - s, 0.0)
        return self.side * value


@dataclass
class Structure:
    name: str
    underlying: str  # "BTC" / "ETH"
    legs: list[Leg]
    contract_value: float  # units of the underlying per contract (BTC options: 0.001)
    direction: str = ""  # "LONG" / "SHORT" view on the underlying
    entry_premiums: list[float] = field(default_factory=list)  # per leg, USD per 1 unit, as filled

    def __post_init__(self):
        if not self.legs:
            raise ValueError("a structure needs at least one leg")
        if len({leg.expiry for leg in self.legs}) != 1:
            raise ValueError("all legs must share one expiry (verticals only)")
        assert_defined_risk(self.legs)

    @property
    def expiry(self) -> dt.datetime:
        return self.legs[0].expiry

    def units(self, leg: Leg) -> float:
        return leg.contracts * self.contract_value

    # ---- money -------------------------------------------------------------------------------------------------
    def net_premium(self, premiums: list[float] | None = None) -> float:
        """USD paid (>0, debit) or received (<0, credit) to open, before fees."""
        prem = premiums if premiums is not None else self.entry_premiums
        if len(prem) != len(self.legs):
            raise ValueError("need one premium per leg")
        return float(sum(leg.side * p * self.units(leg) for leg, p in zip(self.legs, prem)))

    def payoff_at_expiry(self, settle) -> np.ndarray:
        """USD value of the position at settlement (excluding the premium paid or received)."""
        return sum(leg.payoff(settle) * self.units(leg) for leg in self.legs)

    def pnl_at_expiry(self, settle, premiums: list[float] | None = None) -> np.ndarray:
        return self.payoff_at_expiry(settle) - self.net_premium(premiums)

    def _critical_points(self) -> np.ndarray:
        strikes = sorted({leg.strike for leg in self.legs})
        return np.array([0.0, *strikes, strikes[-1] * 10.0])  # P&L is piecewise linear; extremes lie on these

    def max_loss(self, premiums: list[float] | None = None) -> float:
        """Worst P&L at expiry, as a positive USD amount (fees excluded)."""
        pts = self._critical_points()
        return float(max(0.0, -np.min(self.pnl_at_expiry(pts, premiums))))

    def max_profit(self, premiums: list[float] | None = None) -> float:
        """Best P&L at expiry; `inf` when upside is uncapped (net long calls, e.g. a plain long call)."""
        net_long_calls = sum(leg.side * leg.contracts for leg in self.legs if leg.kind == "C")
        if net_long_calls > 0:
            return float("inf")
        pts = self._critical_points()
        return float(np.max(self.pnl_at_expiry(pts, premiums)))

    def breakevens(self, premiums: list[float] | None = None) -> list[float]:
        pts = np.linspace(0.0, self._critical_points()[-2] * 3, 200_001)
        pnl = self.pnl_at_expiry(pts, premiums)
        sign = np.sign(pnl)
        idx = np.where(sign[:-1] * sign[1:] < 0)[0]
        return [float(pts[i] - pnl[i] * (pts[i + 1] - pts[i]) / (pnl[i + 1] - pnl[i])) for i in idx]

    def model_value(self, spot: float, now: dt.datetime, iv_per_leg: list[float]) -> float:
        """Black-Scholes USD value of the position now (long legs positive, short negative)."""
        T = max(0.0, (self.expiry - now).total_seconds())
        return float(sum(leg.side * bs_price(spot, leg.strike, year_fraction(T), iv, leg.kind) * self.units(leg)
                         for leg, iv in zip(self.legs, iv_per_leg)))

    def model_greeks(self, spot: float, now: dt.datetime, iv_per_leg: list[float]) -> dict[str, float]:
        T = year_fraction(max(0.0, (self.expiry - now).total_seconds()))
        total = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta_day": 0.0}
        for leg, iv in zip(self.legs, iv_per_leg):
            g = greeks(spot, leg.strike, T, iv, leg.kind)
            for k in total:
                total[k] += leg.side * float(g[k]) * self.units(leg)
        return total


def assert_defined_risk(legs: list[Leg]) -> None:
    """Each short leg must be covered by long legs of the same kind and expiry, with at least as many contracts. For
    verticals that bounds the loss by the strike width."""
    for kind in ("C", "P"):
        short = sum(leg.contracts for leg in legs if leg.kind == kind and leg.side < 0)
        long_ = sum(leg.contracts for leg in legs if leg.kind == kind and leg.side > 0)
        if short > long_:
            raise UndefinedRiskError(f"uncovered short {kind} leg(s): naked option selling is not allowed")


# ---- builders ------------------------------------------------------------------------------------------------------
def long_call(u: str, strike: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    return Structure("LONG_CALL", u, [Leg("C", strike, expiry, 1, contracts)], cv, "LONG")


def long_put(u: str, strike: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    return Structure("LONG_PUT", u, [Leg("P", strike, expiry, 1, contracts)], cv, "SHORT")


def bull_call_spread(u: str, k_long: float, k_short: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    """Debit: buy the lower-strike call, sell the higher-strike call."""
    if not k_long < k_short:
        raise ValueError("bull call spread: long strike must be below short strike")
    return Structure("BULL_CALL_DEBIT", u, [Leg("C", k_long, expiry, 1, contracts),
                                            Leg("C", k_short, expiry, -1, contracts)], cv, "LONG")


def bear_put_spread(u: str, k_long: float, k_short: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    """Debit: buy the higher-strike put, sell the lower-strike put."""
    if not k_long > k_short:
        raise ValueError("bear put spread: long strike must be above short strike")
    return Structure("BEAR_PUT_DEBIT", u, [Leg("P", k_long, expiry, 1, contracts),
                                           Leg("P", k_short, expiry, -1, contracts)], cv, "SHORT")


def bull_put_spread(u: str, k_short: float, k_long: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    """Credit: sell the higher-strike put, buy the lower-strike put (protection)."""
    if not k_long < k_short:
        raise ValueError("bull put spread: protective long strike must be below the short strike")
    return Structure("BULL_PUT_CREDIT", u, [Leg("P", k_long, expiry, 1, contracts),
                                            Leg("P", k_short, expiry, -1, contracts)], cv, "LONG")


def bear_call_spread(u: str, k_short: float, k_long: float, expiry: dt.datetime, contracts: int, cv: float) -> Structure:
    """Credit: sell the lower-strike call, buy the higher-strike call (protection)."""
    if not k_long > k_short:
        raise ValueError("bear call spread: protective long strike must be above the short strike")
    return Structure("BEAR_CALL_CREDIT", u, [Leg("C", k_long, expiry, 1, contracts),
                                             Leg("C", k_short, expiry, -1, contracts)], cv, "SHORT")


# ---- fees ----------------------------------------------------------------------------------------------------------
def leg_fee(premium: float, spot: float, units: float, commission_rate: float, premium_cap_rate: float,
            gst_rate: float) -> float:
    """USD fee for one fill of one leg, including GST: min(rate × spot × units, cap × premium × units) × (1 + GST)."""
    fee = min(commission_rate * spot * units, premium_cap_rate * max(premium, 0.0) * units)
    return fee * (1.0 + gst_rate)


def structure_fees(s: Structure, premiums: list[float], spot: float, commission_rate: float, premium_cap_rate: float,
                   gst_rate: float) -> float:
    return float(sum(leg_fee(p, spot, s.units(leg), commission_rate, premium_cap_rate, gst_rate)
                     for leg, p in zip(s.legs, premiums)))
