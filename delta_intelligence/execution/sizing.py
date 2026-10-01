"""
Position sizing for defined-risk option structures.

    contracts = floor(0.9 × risk budget / max loss of ONE contract of the structure, incl. round-trip fees)

The risk budget is MAX_RISK_PER_TRADE_PCT × equity. The 0.9 headroom absorbs fills a tick or two worse than quoted.
The result is also capped by the cash available. If even one contract would exceed the budget the size is 0, meaning
NO TRADE (the risk engine would veto it anyway).
"""
from __future__ import annotations

import math

from delta_intelligence.options.structures import Structure, leg_fee


def per_contract_max_loss(st: Structure, prices: list[float], spot: float, commission_rate: float,
                          premium_cap_rate: float, gst_rate: float) -> float:
    """USD max loss of the structure at 1 contract per leg, including entry AND exit fees (exits assumed at the same
    prices, a reasonable fee estimate)."""
    one = Structure(st.name, st.underlying, [type(leg)(leg.kind, leg.strike, leg.expiry, leg.side, 1, leg.symbol)
                                             for leg in st.legs], st.contract_value, st.direction)
    fees = 2 * sum(leg_fee(p, spot, one.contract_value, commission_rate, premium_cap_rate, gst_rate) for p in prices)
    return one.max_loss(prices) + fees


def size_contracts(equity: float, risk_pct: float, per_contract_loss: float, cash_available: float,
                   cash_per_contract: float, headroom: float = 0.9) -> int:
    if per_contract_loss <= 0 or equity <= 0:
        return 0
    by_risk = math.floor(headroom * equity * risk_pct / 100 / per_contract_loss)
    by_cash = math.floor(cash_available / cash_per_contract) if cash_per_contract > 0 else by_risk
    return max(0, min(by_risk, by_cash))
