"""The frozen research matrix: which variants of which strategies are tested, and the option-selection buckets.

Variants are hypotheses declared in advance (2-3 per strategy), NOT the result of a search. Changing anything here
after the first real run changes the protocol hash and fails `tests/test_protocol.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from delta_intelligence.options.selector import SelectorConfig
from lab.base import LabStrategy
from lab.config import (BaseConfig, DonchianConfig, EMAADXConfig, FundingOIConfig, MeanRevConfig, ORBConfig,
                        StraddleConfig, SupertrendVolConfig, SweepMSSConfig, VolBreakoutConfig, VWAPMomentumConfig)
from lab.strategies import (DonchianBreakout, EMATrendADX, FundingOIDivergence, LongStraddleExpansion,
                            MeanReversionExtreme, OpeningRangeBreakout, SupertrendVolFilter, SweepMSS, VolBreakoutTrend,
                            VWAPMomentumBreakout)

# Option-selection buckets (the project's selector reaches ATM and 1-ITM strikes only; no OTM bucket exists).
BUCKETS: dict[str, SelectorConfig] = {
    "ATM_040_060": SelectorConfig(moneyness="ATM", delta_min=0.40, delta_max=0.60),  # the project's default
    "ATM_030_060": SelectorConfig(moneyness="ATM", delta_min=0.30, delta_max=0.60),
    "ITM1_050_070": SelectorConfig(moneyness="ITM1", delta_min=0.50, delta_max=0.70),
}
BASELINE_BUCKET = "ATM_040_060"
DTE_MULTIPLES = (2.5, 4.0)  # tested on the baseline bucket as a sensitivity check
BUCKET_CHECK = tuple(BUCKETS)  # the delta buckets that must be consistent at the baseline DTE


def bucket_selector(bucket_id: str, dte_multiple: float = 2.5) -> SelectorConfig:
    from dataclasses import replace

    return replace(BUCKETS[bucket_id], dte_multiple=dte_multiple)


@dataclass(frozen=True)
class Variant:
    id: str
    strategy_cls: type[LabStrategy]
    config: BaseConfig
    note: str = ""
    legacy: bool = False  # the project's existing SupertrendFlip, re-run for comparison
    legacy_moneyness: str = "ATM"
    tags: tuple = field(default_factory=tuple)

    @property
    def strategy_name(self) -> str:
        return self.strategy_cls.name

    def build(self, timeframe: str = "5m") -> LabStrategy:
        return self.strategy_cls(self.config, timeframe)


V = Variant
VARIANTS: tuple[Variant, ...] = (
    V("S1-base", VolBreakoutTrend, VolBreakoutConfig(), "baseline hypothesis"),
    V("S1-strict", VolBreakoutTrend, VolBreakoutConfig(squeeze_pct=0.10, adx_min=25.0), "tighter compression, stronger trend"),
    V("S1-lenient", VolBreakoutTrend, VolBreakoutConfig(squeeze_pct=0.30, adx_min=15.0), "looser compression, weaker trend"),
    V("S2-n20", DonchianBreakout, DonchianConfig(n=20), "20-bar channel, no filter"),
    V("S2-n40", DonchianBreakout, DonchianConfig(n=40, hold_hours=4.0, max_hold_hours=12.0), "slower 40-bar channel"),
    V("S2-n20-filtered", DonchianBreakout, DonchianConfig(n=20, trend_filter=1, min_rel_vol=1.0), "20-bar + trend + volume"),
    V("S3-base", VWAPMomentumBreakout, VWAPMomentumConfig(), "baseline hypothesis"),
    V("S3-strict", VWAPMomentumBreakout, VWAPMomentumConfig(disp_min_atr=1.5, max_dist_atr=4.0, min_rel_vol=1.5), "larger displacement, more volume"),
    V("S4-base", SweepMSS, SweepMSSConfig(), "5-bar fractals, 6-bar confirmation"),
    V("S4-slow", SweepMSS, SweepMSSConfig(swing_n=8, confirm_window=12, hold_hours=4.0, max_hold_hours=10.0), "8-bar fractals, 12-bar confirmation"),
    V("S5-fade", FundingOIDivergence, FundingOIConfig(mode=0), "positioning weakening -> fade the move"),
    V("S5-follow", FundingOIDivergence, FundingOIConfig(mode=1), "positioning building -> follow the move"),
    V("S6-base", LongStraddleExpansion, StraddleConfig(), "2 of 3 compression measures <= 15th percentile"),
    V("S6-loose", LongStraddleExpansion, StraddleConfig(rv_pct_max=0.25, atr_pct_max=0.25, bbw_pct_max=0.25), "2 of 3 <= 25th percentile"),
    V("S6-all3", LongStraddleExpansion, StraddleConfig(rv_pct_max=0.20, atr_pct_max=0.20, bbw_pct_max=0.20, min_conditions=3), "all 3 <= 20th percentile"),
    V("S7-us-open", OpeningRangeBreakout, ORBConfig(session=1), "13:30 UTC range, 30 min"),
    V("S7-london", OpeningRangeBreakout, ORBConfig(session=2), "07:00 UTC range, 30 min"),
    V("S7-utc-day", OpeningRangeBreakout, ORBConfig(session=0), "00:00 UTC exchange-day range, 30 min"),
    V("S8-20-50", EMATrendADX, EMAADXConfig(), "EMA20/50, ADX >= 20"),
    V("S8-20-50-adx25", EMATrendADX, EMAADXConfig(adx_min=25.0), "EMA20/50, ADX >= 25"),
    V("S8-50-200", EMATrendADX, EMAADXConfig(fast=50, slow=200, hold_hours=6.0, max_hold_hours=16.0), "slow EMA50/200, ADX >= 20"),
    V("S9-atr-band", SupertrendVolFilter, SupertrendVolConfig(filter_kind=1), "Supertrend + ATR percentile 0.2-0.8"),
    V("S9-adx", SupertrendVolFilter, SupertrendVolConfig(filter_kind=2), "Supertrend + ADX >= 20"),
    V("S9-control", SupertrendVolFilter, SupertrendVolConfig(filter_kind=0), "Supertrend with no filter (control)"),
    V("S10-vwap", MeanReversionExtreme, MeanRevConfig(fair=0), "z vs session VWAP >= 2.5"),
    V("S10-ema50", MeanReversionExtreme, MeanRevConfig(fair=1), "z vs EMA50 >= 2.5"),
    V("S10-vwap-z3", MeanReversionExtreme, MeanRevConfig(fair=0, z_min=3.0), "z vs session VWAP >= 3.0"),
)

# The project's two weakly supported active variants, re-run through the SAME pipeline for comparison. They use the
# project's own SupertrendFlip with target_atr_mult 2.25 and ATM / 1-ITM selection (see strategies/active.py).
LEGACY = (
    ("LEGACY-ST-tight-ATM", "ATM"),
    ("LEGACY-ST-tight-ITM1", "ITM1"),
)


def variant_by_id(vid: str) -> Variant:
    for v in VARIANTS:
        if v.id == vid:
            return v
    raise KeyError(vid)


def strategies_in_order() -> list[str]:
    seen: list[str] = []
    for v in VARIANTS:
        if v.strategy_name not in seen:
            seen.append(v.strategy_name)
    return seen
