"""The strategy UNIVERSE the live ranker chooses from: every strategy in the project, each with a unique key.

Sources (36 candidates):
- v3: the 10 plan-v3 strategies (`registry.STRATEGY_CLASSES`);
- legacy_v2: the 14 older strategies (`legacy_v2_library.ALL_STRATEGIES`), keyed with a " (v2)" suffix;
- active: the two Supertrend "tight" paper variants (`active.ACTIVE_VARIANTS`);
- lab: the 10 new families S1-S10 (`lab_library`), baseline variant of each.

`STRATEGY_CLASSES` and `ACTIVE_VARIANTS` are unchanged. Being in the universe means a strategy is RANKED; it does not make
it tradable by itself: the ranker (evidence + analogues + the NO-TRADE rules) and the risk engine decide.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.base import Strategy
from delta_intelligence.strategies.lab_library import config as C
from delta_intelligence.strategies.lab_library import strategies as L
from delta_intelligence.strategies.legacy_v2_library import ALL_STRATEGIES
from delta_intelligence.strategies.registry import STRATEGY_CLASSES


@dataclass(frozen=True)
class Candidate:
    key: str  # unique; used in the ranker, the evidence file, positions and decisions
    source: str  # v3 / legacy_v2 / active / lab
    factory: Callable[[], Strategy]
    moneyness: str = "ATM"

    def build(self) -> Strategy:
        s = self.factory()
        s.name = self.key
        return s


# Baseline variant of each S1-S10 family (the same configurations as research_lab/lab/library.py "-base" variants).
LAB_BASELINES: tuple[tuple[type, object], ...] = (
    (L.VolBreakoutTrend, C.VolBreakoutConfig()), (L.DonchianBreakout, C.DonchianConfig(n=20)),
    (L.VWAPMomentumBreakout, C.VWAPMomentumConfig()), (L.SweepMSS, C.SweepMSSConfig()),
    (L.FundingOIDivergence, C.FundingOIConfig(mode=0)), (L.LongStraddleExpansion, C.StraddleConfig()),
    (L.OpeningRangeBreakout, C.ORBConfig(session=1)), (L.EMATrendADX, C.EMAADXConfig()),
    (L.SupertrendVolFilter, C.SupertrendVolConfig(filter_kind=1)), (L.MeanReversionExtreme, C.MeanRevConfig(fair=0)),
)


def _lab_factory(cls, cfg):
    return lambda: cls(cfg, "5m")


def all_candidates() -> tuple[Candidate, ...]:
    out: list[Candidate] = []
    for name, cls in STRATEGY_CLASSES.items():
        out.append(Candidate(name, "v3", cls))
    for cls in ALL_STRATEGIES:
        out.append(Candidate(f"{cls.name} (v2)", "legacy_v2", cls))
    for v in ACTIVE_VARIANTS:
        out.append(Candidate(v.key, "active", v.strategy, v.moneyness))
    for cls, cfg in LAB_BASELINES:
        out.append(Candidate(cls.name, "lab", _lab_factory(cls, cfg)))
    keys = [c.key for c in out]
    assert len(keys) == len(set(keys)), "strategy keys must be unique"
    return tuple(out)


UNIVERSE: tuple[Candidate, ...] = all_candidates()
BY_KEY: dict[str, Candidate] = {c.key: c for c in UNIVERSE}


def build_universe_frame(ohlcv, features, atm_hourly=None, events=None):
    """The one frame every candidate can read: the v3 frame (`build_strategy_frame`) plus the S1-S10 columns. All causal."""
    from delta_intelligence.strategies.context import build_strategy_frame
    from delta_intelligence.strategies.lab_library.columns import add_lab_columns

    return add_lab_columns(build_strategy_frame(ohlcv, features, atm_hourly, events))
