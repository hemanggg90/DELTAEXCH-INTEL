"""
Exit decisions for open (bought) option positions. Pure function, so it's easy to test; the engine executes the
result.

Checked in this order:
1. SETTLE: expiry has passed. Settle at intrinsic against the settlement index (30-min TWAP).
2. EXPIRY_GUARD: within `expiry_guard_hours` of expiry. Forced exit (user rule: 2 h).
3. UNDERLYING_STOP / TARGET: the underlying's (perp) price crossed the strategy's invalidation / target level.
4. PREMIUM_STOP: the structure's BID value fell to the premium-stop value (entry − 35%).
5. TIME_STOP: the strategy's maximum hold elapsed.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from delta_intelligence.database.models import from_db_time
from delta_intelligence.options.chain import OptionChain


@dataclass
class ExitDecision:
    position_id: str
    action: str  # CLOSE / SETTLE
    reason: str


def decide_exits(items: list[dict], chain: OptionChain, perp_price: dict[str, float], now: dt.datetime,
                 expiry_guard_hours: float) -> list[ExitDecision]:
    out = []
    for it in items:
        p, legs = it["position"], it["legs"]
        expiry = from_db_time(p.expiry)
        if now >= expiry:
            out.append(ExitDecision(p.position_id, "SETTLE", "EXPIRED"))
            continue
        if now >= expiry - dt.timedelta(hours=expiry_guard_hours):
            out.append(ExitDecision(p.position_id, "CLOSE", "EXPIRY_GUARD"))
            continue
        px = perp_price.get(p.underlying)
        if px is not None and p.underlying_stop is not None and p.direction in ("LONG", "SHORT"):
            if (p.direction == "LONG" and px <= p.underlying_stop) or (p.direction == "SHORT" and px >= p.underlying_stop):
                out.append(ExitDecision(p.position_id, "CLOSE", "UNDERLYING_STOP"))
                continue
            if p.underlying_target is not None and (
                    (p.direction == "LONG" and px >= p.underlying_target)
                    or (p.direction == "SHORT" and px <= p.underlying_target)):
                out.append(ExitDecision(p.position_id, "CLOSE", "TARGET"))
                continue
        if p.premium_stop_value is not None:
            bids = [chain.quote(lg.symbol) for lg in legs]
            if all(q is not None and q.bid for q in bids):
                value = sum(q.bid * lg.contracts * p.contract_value for q, lg in zip(bids, legs))
                if value <= p.premium_stop_value:
                    out.append(ExitDecision(p.position_id, "CLOSE", "PREMIUM_STOP"))
                    continue
        if p.time_stop_at is not None and now >= from_db_time(p.time_stop_at):
            out.append(ExitDecision(p.position_id, "CLOSE", "TIME_STOP"))
    return out
