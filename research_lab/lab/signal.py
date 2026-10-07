"""Standardised signal and outcome types.

A strategy answers one of three ways for the latest closed bar:
- `SIGNAL`: a valid setup on the underlying (option selection happens later, elsewhere);
- `NO_SIGNAL`: nothing valid, with the reason (warm-up, missing data, or conditions not met). This is a first-class
  result, never forced into a trade;
- `RISK_VETO`: a valid signal that the project's planner or risk engine refused, with their exact reason. The risk
  engine keeps the last word; this module only reports its answer.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

SIGNAL, NO_SIGNAL, RISK_VETO = "SIGNAL", "NO_SIGNAL", "RISK_VETO"


@dataclass(frozen=True)
class ResearchSignal:
    strategy: str
    version: str
    asset: str
    timeframe: str
    timestamp: pd.Timestamp  # OPEN time of the signal bar
    decision_time: pd.Timestamp  # close of the signal bar: the earliest a trade can start
    direction: str  # CALL / PUT / STRADDLE (long options only)
    strength: float  # 0..1, strategy-specific, NOT a probability
    regime: str  # the project's regime label at the signal bar ("UNKNOWN" if not computed)
    entry_condition: str
    invalidation_condition: str
    stop_logic: str
    target_logic: str
    stop_price: float | None
    target_price: float | None
    underlying_price: float
    confidence: dict = field(default_factory=dict)  # parameters and data flags behind the signal


@dataclass(frozen=True)
class SignalOutcome:
    kind: str  # SIGNAL / NO_SIGNAL / RISK_VETO
    reason: str = ""
    signal: ResearchSignal | None = None

    @property
    def is_trade_candidate(self) -> bool:
        return self.kind == SIGNAL


RiskCheck = Callable[[ResearchSignal, object], "tuple[bool, str]"]  # (signal, Setup) -> (approved, reason)


def project_risk_check(*, strategy, moneyness: str, underlying, chain, index_spot: float, perp_close: float, now,
                       settings, account: dict, account_state, **plan_kwargs) -> RiskCheck:
    """Adapter that runs the PROJECT'S planner and risk engine on a research signal and returns their verdict.

    It only calls `plan_trade` and `evaluate_trade(persist=False)`; it changes nothing in either.
    """
    from delta_intelligence.execution.planner import plan_trade
    from delta_intelligence.risk.risk_engine import evaluate_trade

    def check(signal: ResearchSignal, setup) -> tuple[bool, str]:
        plan = plan_trade(strategy, moneyness, setup, underlying, chain, index_spot, perp_close, now, settings,
                          account, **plan_kwargs)
        if not plan.ok:
            return False, plan.reason
        decision = evaluate_trade(account_state, plan.proposed, persist=False)  # type: ignore[arg-type]
        return bool(decision.approved), decision.reason

    return check
