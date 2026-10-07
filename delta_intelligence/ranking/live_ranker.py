"""
The LIVE ranker: scores every candidate that has a valid setup on the latest closed bar and picks one, or NO TRADE.

It reuses the research ranker unchanged (`research.ranking_core.assess_observations` + `ranking_engine.rank_and_select`), so
whatever `ranking/evaluate_ranker.py` evaluates is exactly what runs here. Its EVIDENCE is the closed backtest trades of
each strategy (net option R, with the six market features at entry) stored in `evidence/ranker_evidence.parquet`, built by
`scripts/build_ranker_evidence.py`. Nothing is invented: a strategy without evidence is ineligible, a missing evidence file
means NO TRADE, and a strategy with no current setup is simply not a candidate this bar.

No ranker threshold is changed here (edge >= 0.05 R after shrinkage, MEDIUM confidence, stable folds, a clear winner).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from delta_intelligence.analogues.analogue_engine import COMPARISON_FEATURES
from delta_intelligence.ranking.ranking_engine import RankingDecision, StrategyScore, rank_and_select
from delta_intelligence.research.ranking_core import assess_observations

EVIDENCE_PATH = Path(__file__).resolve().parents[1] / "evidence" / "ranker_evidence.parquet"
EVIDENCE_COLUMNS = ["strategy_name", "asset", "entry_timestamp", "exit_timestamp", "r_multiple", "underlying_r", *COMPARISON_FEATURES]


@dataclass
class LiveRanking:
    decision: RankingDecision
    table: list[dict] = field(default_factory=list)  # one row per candidate (with or without a setup)
    evidence_note: str = ""

    @property
    def selected(self) -> str | None:
        return None if self.decision.is_no_trade else self.decision.selected_strategy

    @property
    def reason(self) -> str:
        return self.decision.reason


class LiveRanker:
    def __init__(self, evidence: pd.DataFrame | None = None, source: str = "none"):
        self.source = source
        self.evidence = None
        if evidence is not None and len(evidence):
            missing = [c for c in EVIDENCE_COLUMNS if c not in evidence.columns]
            if missing:
                raise ValueError(f"ranker evidence is missing columns {missing}")
            e = evidence.copy()
            e["entry_timestamp"] = pd.to_datetime(e["entry_timestamp"], utc=True)
            e["exit_timestamp"] = pd.to_datetime(e["exit_timestamp"], utc=True)
            self.evidence = e.sort_values("entry_timestamp").reset_index(drop=True)
        self._cache: dict = {}

    @classmethod
    def from_file(cls, path: Path | None = None) -> "LiveRanker":
        p = Path(path or EVIDENCE_PATH)
        if not p.exists():
            return cls(None, f"missing: {p.name}")
        return cls(pd.read_parquet(p), p.name)

    @property
    def available(self) -> bool:
        return self.evidence is not None

    def describe(self) -> str:
        if not self.available:
            return f"no ranker evidence ({self.source}): the ranker can only answer NO TRADE"
        e = self.evidence
        return (f"evidence {self.source}: {len(e):,} closed trades, {e['strategy_name'].nunique()} strategies, "
                f"{e['entry_timestamp'].min():%Y-%m-%d} to {e['entry_timestamp'].max():%Y-%m-%d}")

    def observations(self, key: str, asset: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
        """Closed trades of one strategy on one underlying. As-of: only trades that had exited by `now` (if given)."""
        if not self.available:
            return pd.DataFrame(columns=EVIDENCE_COLUMNS)
        ck = (key, asset)
        if ck not in self._cache:
            e = self.evidence
            self._cache[ck] = e[(e["strategy_name"] == key) & (e["asset"] == asset)]
        obs = self._cache[ck]
        if now is not None:
            obs = obs[obs["exit_timestamp"] <= pd.Timestamp(now)]
        return obs

    def rank(self, asset: str, current_features: dict, with_setup: list[str], all_keys: list[str] | None = None,
             data_quality: str = "OK", now: pd.Timestamp | None = None) -> LiveRanking:
        """Score the strategies that have a setup now (`with_setup`) and select one or NO TRADE."""
        scores: list[StrategyScore] = []
        for key in with_setup:
            obs = self.observations(key, asset, now)
            if not len(obs):
                scores.append(StrategyScore(key, 0.0, None, None, "INSUFFICIENT_DATA", 0, None, False,
                                            "no historical trades (no evidence) for this strategy on this underlying"))
                continue
            scores.append(assess_observations(key, obs, current_features).score)
        if not with_setup:
            decision = RankingDecision([], None, True, "No strategy has a valid setup on the latest bar")
        elif not self.available:
            decision = RankingDecision(scores, None, True, f"NO TRADE: {self.describe()}")
        else:
            decision = rank_and_select(scores, data_quality)
        by = {s.strategy_name: s for s in scores}
        table = []
        for key in all_keys or with_setup:
            s = by.get(key)
            if s is None:
                table.append({"strategy": key, "setup": False, "score": None, "edge_r": None, "confidence": None,
                              "samples": None, "eligible": None, "note": "waiting for setup"})
            else:
                table.append({"strategy": key, "setup": True, "score": s.score, "edge_r": s.edge_mean if s.edge_mean is not None else s.expected_r,
                              "confidence": s.confidence_label, "samples": s.sample_size, "eligible": s.eligible,
                              "note": s.ineligibility_reason or ("SELECTED" if decision.selected_strategy == key and not decision.is_no_trade else "eligible")})
        return LiveRanking(decision, table, self.describe())
