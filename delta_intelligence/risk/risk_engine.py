"""
Deterministic risk engine for a BUYING-ONLY options system.

It depends on nothing in the research layer: only the account state, the proposed trade and the limits in
config/settings.py. It has VETO authority. Every decision (approval or veto) is stored in `risk_events` with the
reason and the result of each check.

Checks, in order (the first failure vetoes):
1. emergency kill switch
2. broker connected
3. data quality OK
4. **buy-only:** any short leg (sell-to-open) is vetoed. The broker independently refuses it as well.
5. daily loss (risk day resets 00:00 IST)
6. drawdown from peak
7. trades today
8. concurrent positions
9. max premium per trade (full premium + fees, % of equity)
10. strategy exposure
11. portfolio exposure
12. correlated-bucket cap (BTC + ETH combined open premium)
13. cash available for premium + fees
14. underlying liquidity (relative volume)
15. expiry guard: the expected hold must end >= `expiry_guard_hours` before expiry
16. per leg: bid/ask spread, open interest, quote size on our side
17. IV-percentile gate (event strategies exempt)
18. breakeven gate (expected move vs extrinsic premium + spread + fees, with margin)

Limits approved by the user on 2026-10-01 and 2026-10-02 (plan v3); see RiskLimits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from delta_intelligence.config.settings import RiskLimits, get_settings
from delta_intelligence.utils.logging_utils import log_event, new_decision_id


@dataclass
class AccountState:
    equity: float
    peak_equity: float
    daily_pnl: float  # since 00:00 IST (realised + unrealised)
    trades_today: int
    open_positions: int
    available_cash: float
    exposure_by_strategy: dict = field(default_factory=dict)  # strategy -> open premium at risk (USD)
    exposure_by_bucket: dict = field(default_factory=dict)  # correlated bucket -> open premium at risk (USD)
    total_exposure: float = 0.0
    broker_connected: bool = True
    kill_switch: bool = False


@dataclass
class LegQuote:
    symbol: str
    side: int  # must be +1 (buy)
    bid: float | None
    ask: float | None
    open_interest: float | None = None
    ask_size: float | None = None  # contracts offered (we buy at the ask)

    @property
    def spread_pct(self) -> float | None:
        if not self.bid or not self.ask or self.bid <= 0 or self.ask <= 0:
            return None
        return (self.ask - self.bid) / ((self.bid + self.ask) / 2) * 100


@dataclass
class ProposedTrade:
    strategy: str
    underlying: str
    bucket: str
    structure: str
    direction: str
    premium_at_risk: float  # USD: premium paid + entry fees (= max loss for bought options)
    cash_required: float
    hours_to_expiry: float
    expected_hold_hours: float
    legs: list[LegQuote]
    data_quality_status: str = "OK"
    relative_volume: float | None = None
    iv_percentile: float | None = None
    is_event_strategy: bool = False
    passes_breakeven: bool = True
    breakeven_detail: str = ""


@dataclass
class RiskDecision:
    decision_id: str
    approved: bool
    reason: str
    checks: dict = field(default_factory=dict)


def _pct(x: float, base: float) -> float:
    return x / base * 100 if base > 0 else math.inf


def evaluate_trade(account: AccountState, trade: ProposedTrade, limits: RiskLimits | None = None,
                   persist: bool = True) -> RiskDecision:
    lim = limits or get_settings().risk
    did = new_decision_id("RISK")
    checks: dict[str, bool] = {}
    eq = account.equity
    shorts = [leg.symbol for leg in trade.legs if leg.side != 1]
    hold_ends_before_guard = trade.hours_to_expiry - lim.expiry_guard_hours >= trade.expected_hold_hours

    steps = [
        ("kill_switch_off", not account.kill_switch, "Emergency kill switch is engaged"),
        ("broker_connected", account.broker_connected, "Broker is disconnected"),
        ("data_quality_ok", trade.data_quality_status == "OK", f"Data quality is {trade.data_quality_status}"),
        ("buy_only", not shorts, f"Sell-to-open is not allowed (short legs: {shorts})"),
        ("daily_loss_within_limit", -_pct(account.daily_pnl, eq) < lim.max_daily_loss_pct,
         f"Daily loss {-_pct(account.daily_pnl, eq):.2f}% has reached the {lim.max_daily_loss_pct}% limit "
         "(resets 00:00 IST)"),
        ("drawdown_within_limit", _pct(account.peak_equity - eq, account.peak_equity) < lim.max_drawdown_pct,
         f"Drawdown {_pct(account.peak_equity - eq, account.peak_equity):.2f}% has reached the "
         f"{lim.max_drawdown_pct}% limit"),
        ("trades_today_within_limit", account.trades_today < lim.max_trades_per_day,
         f"Max trades per day ({lim.max_trades_per_day}) reached"),
        ("concurrent_positions_within_limit", account.open_positions < lim.max_concurrent_positions,
         f"Max concurrent positions ({lim.max_concurrent_positions}) reached"),
        ("premium_per_trade_within_limit",
         math.isfinite(trade.premium_at_risk) and 0 < trade.premium_at_risk
         and _pct(trade.premium_at_risk, eq) <= lim.max_premium_per_trade_pct,
         f"Premium at risk {_pct(trade.premium_at_risk, eq):.2f}% of equity exceeds {lim.max_premium_per_trade_pct}%"),
        ("strategy_exposure_within_limit",
         _pct(account.exposure_by_strategy.get(trade.strategy, 0.0) + trade.premium_at_risk, eq)
         <= lim.max_strategy_exposure_pct, f"Strategy exposure would exceed {lim.max_strategy_exposure_pct}%"),
        ("portfolio_exposure_within_limit", _pct(account.total_exposure + trade.premium_at_risk, eq)
         <= lim.max_portfolio_exposure_pct, f"Portfolio exposure would exceed {lim.max_portfolio_exposure_pct}%"),
        ("correlated_bucket_within_limit",
         not trade.bucket or trade.bucket != "CRYPTO_MAJORS"
         or _pct(account.exposure_by_bucket.get(trade.bucket, 0.0) + trade.premium_at_risk, eq)
         <= lim.correlated_bucket_cap_pct,
         f"Combined open premium in {trade.bucket} would exceed {lim.correlated_bucket_cap_pct}% of equity"),
        ("cash_sufficient", trade.cash_required <= account.available_cash,
         f"Insufficient funds: needs ${trade.cash_required:,.2f}, available ${account.available_cash:,.2f}"),
        ("underlying_liquidity", trade.relative_volume is None or trade.relative_volume >= lim.min_relative_volume,
         f"Underlying relative volume {trade.relative_volume} below {lim.min_relative_volume}"),
        ("expiry_guard_ok", hold_ends_before_guard,
         f"{trade.hours_to_expiry:.1f} h to expiry leaves less than the {trade.expected_hold_hours:.1f} h hold before "
         f"the {lim.expiry_guard_hours} h expiry guard"),
    ]
    for name, ok, reason in steps:
        checks[name] = bool(ok)
        if not ok:
            return _finish(did, False, reason, checks, trade, persist)

    for leg in trade.legs:
        sp = leg.spread_pct
        leg_steps = [
            (f"spread_ok:{leg.symbol}", sp is not None and sp <= lim.max_leg_spread_pct,
             f"{leg.symbol}: no two-sided quote" if sp is None else
             f"{leg.symbol}: bid/ask spread {sp:.1f}% exceeds {lim.max_leg_spread_pct}%"),
            (f"open_interest_ok:{leg.symbol}",
             leg.open_interest is not None and leg.open_interest >= lim.min_leg_open_interest,
             f"{leg.symbol}: open interest {leg.open_interest} below {lim.min_leg_open_interest:.0f}"),
            (f"quote_size_ok:{leg.symbol}", leg.ask_size is not None and leg.ask_size >= lim.min_leg_quote_size,
             f"{leg.symbol}: only {leg.ask_size} contracts offered (min {lim.min_leg_quote_size:.0f})"),
        ]
        for name, ok, reason in leg_steps:
            checks[name] = bool(ok)
            if not ok:
                return _finish(did, False, reason, checks, trade, persist)

    if not trade.is_event_strategy:
        ok = trade.iv_percentile is None or trade.iv_percentile <= lim.iv_percentile_max
        checks["iv_percentile_ok"] = ok
        checks["iv_percentile_known"] = trade.iv_percentile is not None
        if not ok:
            return _finish(did, False, f"ATM IV percentile {trade.iv_percentile:.0f} above {lim.iv_percentile_max:.0f} "
                           "(options too expensive to buy)", checks, trade, persist)
    checks["breakeven_ok"] = trade.passes_breakeven
    if not trade.passes_breakeven:
        return _finish(did, False, f"Breakeven gate: {trade.breakeven_detail or 'expected move too small for the cost'}",
                       checks, trade, persist)
    return _finish(did, True, "All risk checks passed", checks, trade, persist)


def _finish(did: str, approved: bool, reason: str, checks: dict, trade: ProposedTrade, persist: bool) -> RiskDecision:
    log_event("risk_engine", f"{'APPROVED' if approved else 'VETO'}: {reason}", level="INFO" if approved else "WARNING",
              decision_id=did, strategy=trade.strategy, underlying=trade.underlying)
    if persist:
        try:
            from delta_intelligence.database.db import get_session, is_initialized
            from delta_intelligence.database.models import RiskEvent

            if is_initialized():
                with get_session() as s:
                    s.add(RiskEvent(decision_id=did, event_type="APPROVED" if approved else "VETO", reason=reason,
                                    strategy=trade.strategy, underlying=trade.underlying,
                                    details={"checks": checks, "structure": trade.structure,
                                             "premium_at_risk": trade.premium_at_risk}))
        except Exception as exc:
            log_event("risk_engine", f"could not persist risk event {did}: {exc}", level="ERROR")
    return RiskDecision(did, approved, reason, checks)
