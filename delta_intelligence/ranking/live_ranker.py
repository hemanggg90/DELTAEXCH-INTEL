"""
The LIVE ranker: scores EVERY strategy on the current market state, shows the full leaderboard, and picks the best-ranked
strategy that is actually signalling, or NO TRADE.

It reuses the research ranker unchanged (`research.ranking_core.assess_observations` + `ranking_engine.rank_and_select`), so
whatever `ranking/evaluate_ranker.py` evaluates is exactly what runs here. Its EVIDENCE is the closed backtest trades of
each strategy (net option R, with the six market features at entry) stored in `evidence/ranker_evidence.parquet`, built by
`scripts/build_ranker_evidence.py`.

- **Leaderboard:** every candidate is scored every bar, with or without a signal. A strategy without evidence is shown as such
  (never hidden, never given an invented score).
- **Selection:** a strategy can only be traded when it has a valid setup now. `rank_and_select` runs over the ELIGIBLE strategies
  that have a setup (same thresholds: shrunk edge >= 0.05 R, MEDIUM confidence, stable folds, a clear winner), so the traded
  strategy is the best-ranked one that is signalling. The overall leader is reported too, with whether it is waiting for a signal.
- A missing evidence file means NO TRADE. No ranker threshold is changed here.
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
    table: list[dict] = field(default_factory=list)  # the FULL leaderboard, best first
    evidence_note: str = ""
    leader: dict | None = None  # the best-ranked eligible strategy overall (may be waiting for its signal)

    @property
    def selected(self) -> str | None:
        return None if self.decision.is_no_trade else self.decision.selected_strategy

    @property
    def reason(self) -> str:
        return self.decision.reason


def _num(x, nd: int = 4):
    return None if x is None else round(float(x), nd)


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

    def score_all(self, asset: str, current_features: dict, keys: list[str], now: pd.Timestamp | None = None) -> dict[str, StrategyScore]:
        out: dict[str, StrategyScore] = {}
        for key in dict.fromkeys(keys):
            obs = self.observations(key, asset, now)
            if not len(obs):
                out[key] = StrategyScore(key, 0.0, None, None, "INSUFFICIENT_DATA", 0, None, False,
                                         "no historical trades (no evidence) for this strategy on this underlying")
            else:
                out[key] = assess_observations(key, obs, current_features).score
        return out

    def rank(self, asset: str, current_features: dict, with_setup: list[str], all_keys: list[str] | None = None,
             data_quality: str = "OK", now: pd.Timestamp | None = None) -> LiveRanking:
        """Score ALL strategies (`all_keys`) for the leaderboard; select among the eligible ones that have a setup now."""
        keys = list(dict.fromkeys([*(all_keys or []), *with_setup]))
        scores = self.score_all(asset, current_features, keys, now)
        has = set(with_setup)
        cand = [scores[k] for k in with_setup]
        if not with_setup:
            decision = RankingDecision([], None, True, "No strategy has a valid setup on the latest bar")
        elif not self.available:
            decision = RankingDecision(cand, None, True, f"NO TRADE: {self.describe()}")
        else:
            decision = rank_and_select(cand, data_quality)
        selected = None if decision.is_no_trade else decision.selected_strategy
        ordered = sorted(scores.values(), key=lambda s: (s.eligible, s.score), reverse=True)
        table = []
        for i, s in enumerate(ordered, 1):
            if s.strategy_name == selected:
                note = "SELECTED"
            elif s.ineligibility_reason:
                note = s.ineligibility_reason
            elif s.strategy_name in has:
                note = "eligible and signalling, but not chosen (" + decision.reason.split(":")[0][:80] + ")" if decision.is_no_trade \
                    else "eligible and signalling, ranked below the selected strategy"
            else:
                note = "eligible, waiting for its signal"
            table.append({"rank": i, "strategy": s.strategy_name, "setup": s.strategy_name in has, "score": _num(s.score),
                          "edge_r": _num(s.edge_mean if s.edge_mean is not None else s.expected_r), "confidence": s.confidence_label,
                          "samples": s.sample_size, "eligible": s.eligible, "selected": s.strategy_name == selected, "note": note})
        top = next((r for r in table if r["eligible"]), None)
        leader = None if top is None else {"strategy": top["strategy"], "score": top["score"], "has_setup": top["setup"]}
        return LiveRanking(decision, table, self.describe(), leader)
