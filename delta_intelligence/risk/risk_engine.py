"""
Deterministic risk engine for defined-risk option trades.

It depends on nothing in the research layer: only the account state, the proposed trade and the limits in
config/settings.py. It has VETO authority; nothing downstream can override a rejection. Every decision (approval or
veto) is stored in `risk_events` with the reason and the result of each check.

Checks, in order (the first failure vetoes):
1. emergency kill switch
2. broker connected
3. data quality OK
4. daily loss (IST risk day)
5. drawdown from peak
6. trades today
7. concurrent positions
8. defined risk (finite, positive max loss; no naked short legs)
9. risk per trade (max loss incl. fees, % of equity)
10. strategy exposure
11. portfolio exposure (sum of open max losses)
12. cash (debit + fees, or the margin reserved for a credit spread)
13. underlying liquidity (relative volume)
14. time to expiry (>= 6 h)
15. bid/ask spread per leg (<= 10% of mid)
16. open interest / quote size per leg (OFF until the user sets a value)
17. IV / realised vol for debit structures (<= 1.5)

Limits approved by the user on 2026-10-01 and 2026-10-02. See RiskLimits.
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
    daily_pnl: float  # since the start of the IST risk day (realised + unrealised)
    trades_today: int
    open_positions: int
    available_cash: float
    exposure_by_strategy: dict = field(default_factory=dict)  # strategy -> sum of open max losses (USD)
    total_exposure: float = 0.0
    broker_connected: bool = True
    kill_switch: bool = False


@dataclass
class LegQuote:
    symbol: str
    side: int
    bid: float | None
    ask: float | None
    open_interest: float | None = None
    quote_size: float | None = None

    @property
    def spread_pct(self) -> float | None:
        if not self.bid or not self.ask or self.bid <= 0 or self.ask <= 0:
            return None
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 100


@dataclass
class ProposedTrade:
    strategy: str
    underlying: str
    structure: str
    direction: str
    max_loss: float  # USD incl. entry fees
    net_premium: float  # USD (>0 debit)
    cash_required: float  # debit + fees, or reserved margin + fees for a credit spread
    hours_to_expiry: float
    legs: list[LegQuote]
    defined_risk: bool = True
    data_quality_status: str = "OK"
    relative_volume: float | None = None
    atm_iv: float | None = None
    realized_vol: float | None = None


@dataclass
class RiskDecision:
    decision_id: str
    approved: bool
    reason: str
    checks: dict = field(default_factory=dict)


def _pct(x: float, equity: float) -> float:
    return x / equity * 100 if equity > 0 else math.inf


def evaluate_trade(account: AccountState, trade: ProposedTrade, limits: RiskLimits | None = None,
                   persist: bool = True) -> RiskDecision:
    lim = limits or get_settings().risk
    did = new_decision_id("RISK")
    checks: dict[str, bool] = {}

    def veto(reason: str) -> RiskDecision:
        return _finish(did, False, reason, checks, trade, persist)

    def check(name: str, ok: bool, reason: str) -> RiskDecision | None:
        checks[name] = bool(ok)
        return None if ok else veto(reason)

    steps = [
        ("kill_switch_off", not account.kill_switch, "Emergency kill switch is engaged"),
        ("broker_connected", account.broker_connected, "Broker is disconnected"),
        ("data_quality_ok", trade.data_quality_status == "OK", f"Data quality is {trade.data_quality_status}"),
        ("daily_loss_within_limit", -_pct(account.daily_pnl, account.equity) < lim.max_daily_loss_pct,
         f"Daily loss {-_pct(account.daily_pnl, account.equity):.2f}% has reached the {lim.max_daily_loss_pct}% limit"),
        ("drawdown_within_limit", _pct(account.peak_equity - account.equity, account.peak_equity) < lim.max_drawdown_pct,
         f"Drawdown {_pct(account.peak_equity - account.equity, account.peak_equity):.2f}% has reached the "
         f"{lim.max_drawdown_pct}% limit"),
        ("trades_today_within_limit", account.trades_today < lim.max_trades_per_day,
         f"Max trades per day ({lim.max_trades_per_day}) reached"),
        ("concurrent_positions_within_limit", account.open_positions < lim.max_concurrent_positions,
         f"Max concurrent positions ({lim.max_concurrent_positions}) reached"),
        ("defined_risk", trade.defined_risk and math.isfinite(trade.max_loss) and trade.max_loss > 0,
         "Structure does not have a finite, positive max loss (naked short legs are never allowed)"),
        ("risk_per_trade_within_limit", _pct(trade.max_loss, account.equity) <= lim.max_risk_per_trade_pct,
         f"Max loss {_pct(trade.max_loss, account.equity):.2f}% of equity exceeds {lim.max_risk_per_trade_pct}%"),
        ("strategy_exposure_within_limit",
         _pct(account.exposure_by_strategy.get(trade.strategy, 0.0) + trade.max_loss, account.equity)
         <= lim.max_strategy_exposure_pct,
         f"Strategy exposure would exceed {lim.max_strategy_exposure_pct}% of equity"),
        ("portfolio_exposure_within_limit",
         _pct(account.total_exposure + trade.max_loss, account.equity) <= lim.max_portfolio_exposure_pct,
         f"Portfolio exposure would exceed {lim.max_portfolio_exposure_pct}% of equity"),
        ("cash_sufficient", trade.cash_required <= account.available_cash,
         f"Insufficient funds: needs ${trade.cash_required:,.2f}, available ${account.available_cash:,.2f}"),
        ("underlying_liquidity", trade.relative_volume is None or trade.relative_volume >= lim.min_relative_volume,
         f"Underlying relative volume {trade.relative_volume} below {lim.min_relative_volume}"),
        ("time_to_expiry_ok", trade.hours_to_expiry >= lim.min_hours_to_expiry,
         f"Only {trade.hours_to_expiry:.1f} h to expiry; minimum is {lim.min_hours_to_expiry} h"),
    ]
    for name, ok, reason in steps:
        r = check(name, ok, reason)
        if r is not None:
            return r

    for leg in trade.legs:
        sp = leg.spread_pct
        r = check(f"spread_ok:{leg.symbol}", sp is not None and sp <= lim.max_leg_spread_pct,
                  f"{leg.symbol}: no two-sided quote" if sp is None else
                  f"{leg.symbol}: bid/ask spread {sp:.1f}% of mid exceeds {lim.max_leg_spread_pct}%")
        if r is not None:
            return r
        if lim.min_leg_open_interest is not None:
            r = check(f"open_interest_ok:{leg.symbol}",
                      leg.open_interest is not None and leg.open_interest >= lim.min_leg_open_interest,
                      f"{leg.symbol}: open interest {leg.open_interest} below {lim.min_leg_open_interest}")
            if r is not None:
                return r

    if trade.net_premium > 0:  # debit structure: don't overpay for volatility
        ratio = (trade.atm_iv / trade.realized_vol) if trade.atm_iv and trade.realized_vol else None
        r = check("iv_rv_ok_for_debit", ratio is None or ratio <= lim.max_iv_to_rv_for_debit,
                  f"Implied vol is {ratio:.2f}x realised vol (limit {lim.max_iv_to_rv_for_debit}x for debit trades)"
                  if ratio else "")
        if r is not None:
            return r
        if ratio is None:
            checks["iv_rv_ok_for_debit"] = True  # unknown ratio: not blocking, recorded below
            checks["iv_rv_unknown"] = True

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
                                             "max_loss": trade.max_loss, "cash_required": trade.cash_required}))
        except Exception as exc:  # the decision stands even if the audit write fails; say so loudly
            log_event("risk_engine", f"could not persist risk event {did}: {exc}", level="ERROR")
    return RiskDecision(did, approved, reason, checks)
