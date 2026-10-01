"""
Option structures for a BUYING-ONLY system (user decision, 2026-10-02): long call, long put, long straddle and long
strangle.

Every leg is bought. A structure with a short leg cannot be constructed (`SellToOpenRejected`); the broker and the
risk engine enforce the same rule independently. Max loss = premium paid + fees.

Units:
- Prices are in USD per 1 unit of the underlying, as Delta quotes them.
- A leg's `contracts` × `contract_value` (BTC 0.001, ETH 0.01, XAUT 0.001) gives units of the underlying.

Fees follow Delta's option schedule (product fields, verified 2026-10-02): commission_rate × notional, capped at
premium_cap_rate × premium value, per leg per fill, plus GST on the fee (18%, UNVERIFIED).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from delta_intelligence.options.pricing import bs_price, greeks, year_fraction


class SellToOpenRejected(ValueError):
    """Selling an option to open a position is not allowed in this system, at any layer."""


@dataclass(frozen=True)
class Leg:
    kind: str  # "C" or "P"
    strike: float
    expiry: dt.datetime  # tz-aware settlement instant
    side: int  # always +1 (bought); kept as a field so stored records stay explicit
    contracts: int
    symbol: str = ""

    def __post_init__(self):
        if self.kind not in ("C", "P"):
            raise ValueError("kind must be 'C' or 'P'")
        if self.side != 1:
            raise SellToOpenRejected(f"leg {self.symbol or self.kind + str(self.strike)} has side {self.side}: "
                                     "only bought (long) legs are allowed")
        if self.contracts <= 0:
            raise ValueError("contracts must be positive")
        if self.expiry.tzinfo is None:
            raise ValueError("expiry must be timezone-aware")

    def payoff(self, settle) -> np.ndarray:
        s = np.asarray(settle, dtype="float64")
        return np.maximum(s - self.strike, 0.0) if self.kind == "C" else np.maximum(self.strike - s, 0.0)


STRUCTURE_NAMES = ("LONG_CALL", "LONG_PUT", "LONG_STRADDLE", "LONG_STRANGLE")


@dataclass
class Structure:
    name: str
    underlying: str
    legs: list[Leg]
    contract_value: float
    direction: str = ""  # LONG / SHORT (view on the underlying) or VOL for straddles/strangles
    entry_premiums: list[float] = field(default_factory=list)

    def __post_init__(self):
        if self.name not in STRUCTURE_NAMES:
            raise SellToOpenRejected(f"structure {self.name!r} is not a buying-only structure")
        if not self.legs:
            raise ValueError("a structure needs at least one leg")
        if len({leg.expiry for leg in self.legs}) != 1:
            raise ValueError("all legs must share one expiry")
        assert_buy_only(self.legs)

    @property
    def expiry(self) -> dt.datetime:
        return self.legs[0].expiry

    def units(self, leg: Leg) -> float:
        return leg.contracts * self.contract_value

    def premium_paid(self, premiums: list[float] | None = None) -> float:
        """USD paid to open (before fees). Always >= 0 for a buying-only structure."""
        prem = premiums if premiums is not None else self.entry_premiums
        if len(prem) != len(self.legs):
            raise ValueError("need one premium per leg")
        return float(sum(p * self.units(leg) for leg, p in zip(self.legs, prem)))

    net_premium = premium_paid  # compatibility alias

    def payoff_at_expiry(self, settle) -> np.ndarray:
        return sum(leg.payoff(settle) * self.units(leg) for leg in self.legs)

    def pnl_at_expiry(self, settle, premiums: list[float] | None = None) -> np.ndarray:
        return self.payoff_at_expiry(settle) - self.premium_paid(premiums)

    def max_loss(self, premiums: list[float] | None = None) -> float:
        """For bought options the worst case is losing the whole premium (fees excluded)."""
        return self.premium_paid(premiums)

    def max_profit(self, premiums: list[float] | None = None) -> float:
        if any(leg.kind == "C" for leg in self.legs):
            return float("inf")
        # puts only: best case is settlement at 0
        return float(self.payoff_at_expiry(0.0) - self.premium_paid(premiums))

    def breakevens(self, premiums: list[float] | None = None) -> list[float]:
        strikes = sorted({leg.strike for leg in self.legs})
        pts = np.linspace(0.0, strikes[-1] * 3, 300_001)
        pnl = self.pnl_at_expiry(pts, premiums)
        idx = np.where(np.sign(pnl[:-1]) * np.sign(pnl[1:]) < 0)[0]
        return [float(pts[i] - pnl[i] * (pts[i + 1] - pts[i]) / (pnl[i + 1] - pnl[i])) for i in idx]

    def model_value(self, spot: float, now: dt.datetime, iv_per_leg: list[float]) -> float:
        T = max(0.0, (self.expiry - now).total_seconds())
        return float(sum(bs_price(spot, leg.strike, year_fraction(T), iv, leg.kind) * self.units(leg)
                         for leg, iv in zip(self.legs, iv_per_leg)))

    def model_greeks(self, spot: float, now: dt.datetime, iv_per_leg: list[float]) -> dict[str, float]:
        T = year_fraction(max(0.0, (self.expiry - now).total_seconds()))
        total = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta_day": 0.0}
        for leg, iv in zip(self.legs, iv_per_leg):
            g = greeks(spot, leg.strike, T, iv, leg.kind)
            for k in total:
                total[k] += float(g[k]) * self.units(leg)
        return total


def assert_buy_only(legs: list[Leg]) -> None:
    for leg in legs:
        if leg.side != 1:
            raise SellToOpenRejected("sell-to-open is not allowed: every leg must be bought")


# ---- builders ------------------------------------------------------------------------------------------------------
def long_call(u: str, strike: float, expiry: dt.datetime, contracts: int, cv: float, symbol: str = "") -> Structure:
    return Structure("LONG_CALL", u, [Leg("C", strike, expiry, 1, contracts, symbol)], cv, "LONG")


def long_put(u: str, strike: float, expiry: dt.datetime, contracts: int, cv: float, symbol: str = "") -> Structure:
    return Structure("LONG_PUT", u, [Leg("P", strike, expiry, 1, contracts, symbol)], cv, "SHORT")


def long_straddle(u: str, strike: float, expiry: dt.datetime, contracts: int, cv: float,
                  symbols: tuple[str, str] = ("", "")) -> Structure:
    return Structure("LONG_STRADDLE", u, [Leg("C", strike, expiry, 1, contracts, symbols[0]),
                                          Leg("P", strike, expiry, 1, contracts, symbols[1])], cv, "VOL")


def long_strangle(u: str, k_put: float, k_call: float, expiry: dt.datetime, contracts: int, cv: float,
                  symbols: tuple[str, str] = ("", "")) -> Structure:
    if not k_put < k_call:
        raise ValueError("strangle: put strike must be below call strike")
    return Structure("LONG_STRANGLE", u, [Leg("C", k_call, expiry, 1, contracts, symbols[0]),
                                          Leg("P", k_put, expiry, 1, contracts, symbols[1])], cv, "VOL")


# ---- fees ----------------------------------------------------------------------------------------------------------
def leg_fee(premium: float, spot: float, units: float, commission_rate: float, premium_cap_rate: float,
            gst_rate: float) -> float:
    """USD fee for one fill of one leg incl. GST: min(rate × spot × units, cap × premium × units) × (1 + GST)."""
    fee = min(commission_rate * spot * units, premium_cap_rate * max(premium, 0.0) * units)
    return fee * (1.0 + gst_rate)


def structure_fees(s: Structure, premiums: list[float], spot: float, commission_rate: float, premium_cap_rate: float,
                   gst_rate: float) -> float:
    return float(sum(leg_fee(p, spot, s.units(leg), commission_rate, premium_cap_rate, gst_rate)
                     for leg, p in zip(s.legs, premiums)))
