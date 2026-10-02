"""
Strategies ACTIVE for (paper) trading.

User decision, 2026-10-02: paper-trade the two variants that stayed positive on unseen data in the strategy search
(docs/research/STRATEGY_SEARCH.md). Both are Supertrend Trend Following with the "tight" target (target at 2.25 ATR
instead of 3.0; the stop is the Supertrend line):
- #1 buys the ATM option;
- #2 buys the 1-strike-ITM option.

Evidence is weak: 33-47 holdout trades, positive on BTC, ~0 on ETH, and selected from 270 variants, where random
signals produced a similar number of "winners". This is a paper-trading experiment, not a validated edge. Both fire on
the same Supertrend flip, so the two option choices are compared side by side.

Every other strategy runs in research/monitor mode only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from delta_intelligence.strategies.base import Strategy
from delta_intelligence.strategies.legacy_v2_library import SupertrendFlip


@dataclass(frozen=True)
class ActiveVariant:
    key: str  # unique name used on positions/decisions
    cls: type[Strategy]
    params: dict = field(default_factory=dict)
    moneyness: str = "ATM"
    hold_mult: float = 1.0

    def strategy(self) -> Strategy:
        s = self.cls(self.params)
        s.name = self.key
        if self.hold_mult != 1:
            s.expected_hold_bars = int(s.expected_hold_bars * self.hold_mult)
            s.max_hold_bars = int(s.max_hold_bars * self.hold_mult)
        return s


ACTIVE_VARIANTS: tuple[ActiveVariant, ...] = (
    ActiveVariant("Supertrend tight / ATM", SupertrendFlip, {"target_atr_mult": 2.25}, "ATM"),
    ActiveVariant("Supertrend tight / 1-ITM", SupertrendFlip, {"target_atr_mult": 2.25}, "ITM1"),
)
