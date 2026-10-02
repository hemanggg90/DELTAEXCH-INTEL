"""
Live trade planner: setup → bought option structure from the LIVE chain → premium plan → size → risk proposal.

The same selector rules as the backtest:
- expiry: DTE >= 2.5 × hold, hold ends before the 2 h guard, at most 45 days;
- strike: ATM or 1-ITM with |delta| 0.40-0.60, using the live chain's delta.

Sizing: floor(0.9 × 0.5% of equity / per-contract premium incl. round-trip fees), capped by cash.

Returns a PlanResult; the RISK ENGINE (not this module) decides.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from delta_intelligence.brokers.paper_broker import TradePlan
from delta_intelligence.config.settings import Settings
from delta_intelligence.config.watchlist import Underlying
from delta_intelligence.execution.sizing import per_contract_max_loss, size_contracts
from delta_intelligence.options.chain import OptionChain
from delta_intelligence.options.premium_model import PremiumPlan, plan_premium
from delta_intelligence.options.selector import SelectorConfig, build_long, choose_expiry
from delta_intelligence.options.structures import leg_fee
from delta_intelligence.risk.risk_engine import LegQuote, ProposedTrade
from delta_intelligence.strategies.base import Setup, Strategy

CONTRACT_VALUE = {"BTC": 0.001, "ETH": 0.01, "XAUT": 0.001}


@dataclass
class PlanResult:
    ok: bool
    reason: str = ""
    plan: TradePlan | None = None
    proposed: ProposedTrade | None = None
    premium: PremiumPlan | None = None
    details: dict = field(default_factory=dict)


def plan_trade(strategy: Strategy, moneyness: str, setup: Setup, underlying: Underlying, chain: OptionChain,
               index_spot: float, perp_close: float, now: dt.datetime, settings: Settings, account: dict,
               relative_volume: float | None = None, iv_percentile: float | None = None,
               data_quality: str = "OK") -> PlanResult:
    lim, costs = settings.risk, settings.costs
    cfg = SelectorConfig(moneyness=moneyness, delta_min=lim.delta_min, delta_max=lim.delta_max,
                         dte_multiple=lim.dte_multiple, expiry_guard_hours=lim.expiry_guard_hours)
    asset = underlying.asset
    hold_h = strategy.expected_hold_bars * 5 / 60.0
    now_ts = pd.Timestamp(now)
    expiry = choose_expiry(now_ts, hold_h, chain.expiries(asset), cfg)
    if expiry is None:
        return PlanResult(False, f"no listed expiry fits a {hold_h:.1f} h hold (DTE >= {cfg.dte_multiple}x, guard)")
    strikes = chain.strikes(asset, expiry)

    def abs_delta(kind, k):
        sym = chain.symbol(asset, kind, k, expiry)
        q = chain.quote(sym) if sym else None
        return abs(q.delta) if q and q.delta is not None else None

    sym_for = lambda k, x, e: chain.symbol(asset, k, x, e) or ""  # noqa: E731
    try:
        st1 = build_long(strategy.policy, setup.direction, asset, index_spot, expiry, strikes, 1,
                         CONTRACT_VALUE.get(asset, 0.001), abs_delta, cfg, sym_for)
    except ValueError as exc:
        return PlanResult(False, str(exc))
    quotes = [chain.quote(leg.symbol) for leg in st1.legs]
    if any(q is None or not q.ask or not q.bid for q in quotes):
        return PlanResult(False, "a leg has no two-sided quote")
    cv = quotes[0].contract_value or CONTRACT_VALUE.get(asset, 0.001)
    asks = [q.ask for q in quotes]
    per = per_contract_max_loss(st1, asks, index_spot, costs.fallback_taker_rate, costs.fallback_premium_cap_rate,
                                costs.gst_rate)
    n = size_contracts(account["equity"], lim.max_premium_per_trade_pct, per, account["available_cash"], per)
    if n <= 0:
        return PlanResult(False, f"one contract costs ${per:.2f}, above the {lim.max_premium_per_trade_pct}% budget")
    st = build_long(strategy.policy, setup.direction, asset, index_spot, expiry, strikes, n, cv, abs_delta, cfg,
                    sym_for)
    units = n * cv
    fees_in = sum(leg_fee(a, index_spot, units, costs.fallback_taker_rate, costs.fallback_premium_cap_rate,
                          costs.gst_rate) for a in asks)
    paid = st.premium_paid(asks)
    basis = index_spot / perp_close if perp_close else 1.0
    ivs = [q.mark_iv or 0.5 for q in quotes]
    prem = plan_premium(st, index_spot, now, ivs, asks, [q.ask - q.mid for q in quotes], 2 * fees_in / units,
                        setup.stop_price * basis if setup.direction != "VOL" else None,
                        setup.target_price * basis if setup.direction != "VOL" else None, hold_h,
                        lim.premium_stop_pct, lim.breakeven_margin, setup.meta.get("expected_abs_move"))
    tte_h = (expiry - now_ts).total_seconds() / 3600.0
    proposed = ProposedTrade(
        strategy=strategy.name, underlying=asset, bucket=underlying.correlated_bucket, structure=st.name,
        direction=setup.direction, premium_at_risk=paid + fees_in, cash_required=paid + fees_in,
        hours_to_expiry=tte_h, expected_hold_hours=hold_h,
        legs=[LegQuote(q.symbol, 1, q.bid, q.ask, q.open_interest, q.ask_size) for q in quotes],
        data_quality_status=data_quality, relative_volume=relative_volume, iv_percentile=iv_percentile,
        is_event_strategy=strategy.is_event, passes_breakeven=prem.passes_breakeven,
        breakeven_detail=f"expected move {prem.expected_move:.1f} < required {prem.required_move:.1f}")
    plan = TradePlan(strategy.name, st, setup.stop_price, setup.target_price,
                     time_stop_at=now + dt.timedelta(minutes=5 * strategy.max_hold_bars),
                     premium_stop_pct=lim.premium_stop_pct, expected_hold_hours=hold_h)
    return PlanResult(True, "planned", plan, proposed, prem,
                      {"expiry": expiry, "contracts": n, "per_contract": per, "tte_hours": tte_h})
