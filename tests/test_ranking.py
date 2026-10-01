from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.analogues.analogue_engine import (
    cluster_robust_stderr,
    conditional_metrics_from_analogues,
    find_analogues,
)
from delta_intelligence.ranking.evaluate_ranker import bootstrap_ci, evaluate, summarize
from delta_intelligence.ranking.ranking_engine import StrategyScore, rank_and_select, score_strategy
from delta_intelligence.research.ranking_core import assess_observations, stability


def obs_frame(n=60, r_mean=0.3, days=20, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "trend_slope": rng.normal(0, 1e-4, n), "momentum_20": rng.normal(0, 0.003, n),
        "atr_percentile_100": rng.uniform(0, 1, n), "vol_expansion": rng.uniform(0.6, 1.6, n),
        "relative_volume": rng.uniform(0.5, 2, n), "vwap_distance_pct": rng.normal(0, 0.3, n),
        "r_multiple": rng.normal(r_mean, 0.8, n),
        "entry_timestamp": pd.Timestamp("2026-06-01T00:00Z") + pd.to_timedelta(np.sort(rng.integers(0, days * 24, n)), unit="h"),
    })


def test_analogues_pick_nearest_and_skip_uninformative() -> None:
    obs = obs_frame()
    q = obs.iloc[5].to_dict()
    top = find_analogues(q, obs, top_k=5)
    assert top.index[0] == 5 and top["similarity_score"].iloc[0] == pytest.approx(1.0)
    assert len(find_analogues({k: np.nan for k in q}, obs)) == 0


def test_confidence_counts_days_not_rows() -> None:
    obs = obs_frame(n=30, days=1)
    m = conditional_metrics_from_analogues(obs)
    assert m["n_days"] <= 2 and m["confidence_label"] == "LOW"
    assert conditional_metrics_from_analogues(obs.head(5))["confidence_label"] == "INSUFFICIENT_DATA"


def test_cluster_robust_se_exceeds_iid_for_clustered_data() -> None:
    v = np.repeat([1.0, -1.0, 1.0, -1.0, 1.0], 10) + np.random.default_rng(1).normal(0, 0.01, 50)
    iid = v.std(ddof=1) / np.sqrt(len(v))
    assert cluster_robust_stderr(v, np.repeat(np.arange(5), 10)) > 2 * iid


def test_no_trade_rules() -> None:
    good = StrategyScore("A", 1.0, 0.5, 0.6, "HIGH", 50, -2, True, edge_mean=0.5, edge_stderr=0.05)
    assert rank_and_select([good], "DEGRADED").is_no_trade
    assert rank_and_select([StrategyScore("B", 0.2, 0.0, 0.5, "HIGH", 50, -2, False, "below edge")]).is_no_trade
    twin = StrategyScore("C", 0.99, 0.5, 0.6, "HIGH", 50, -2, True, edge_mean=0.49, edge_stderr=0.05)
    assert rank_and_select([good, twin]).is_no_trade  # indistinguishable
    d = rank_and_select([good])
    assert not d.is_no_trade and d.selected_strategy == "A"


def test_shrinkage_and_eligibility() -> None:
    s = score_strategy({"strategy_name": "X", "confidence_label": "HIGH", "sample_size": 30, "expected_r": 0.8,
                        "expected_r_stderr": 0.4, "prob_positive_return": 0.6, "global_expected_r": 0.0}, -3.0)
    assert 0 < s.edge_mean < 0.8  # pulled toward the global mean
    weak = score_strategy({"strategy_name": "Y", "confidence_label": "HIGH", "sample_size": 30, "expected_r": 0.02,
                           "expected_r_stderr": 0.1, "prob_positive_return": 0.5, "global_expected_r": 0.0}, -1.0)
    assert not weak.eligible


def test_stability_requires_positive_folds() -> None:
    obs = obs_frame(n=40)
    obs.loc[obs.index[:30], "r_multiple"] = -1.0
    assert stability(obs)["stability_positive_frac"] < 0.5
    assert assess_observations("S", obs, obs.iloc[0].to_dict()).score.eligible is False


class T:  # minimal stand-in for OptionTrade
    def __init__(self, e, x, r, ts):
        self.entry_idx, self.exit_idx, self.net_r, self.underlying_r, self.entry_time = e, x, r, r, ts


def test_evaluator_only_uses_closed_trades(monkeypatch) -> None:
    n = 400
    feats = pd.DataFrame({"timestamp": pd.date_range("2026-06-01T00:00Z", periods=n, freq="5min"),
                          **{c: np.random.default_rng(2).normal(size=n) for c in
                             ("trend_slope", "momentum_20", "atr_percentile_100", "vol_expansion", "relative_volume",
                              "vwap_distance_pct")}})
    trades = {"A": [T(i, i + 50, 1.0, feats["timestamp"].iloc[i]) for i in range(10, 390, 7)],
              "B": [T(i, i + 50, -1.0, feats["timestamp"].iloc[i]) for i in range(12, 390, 9)]}
    seen = []
    import delta_intelligence.ranking.evaluate_ranker as er
    real = er.assess_observations

    def spy(name, obs, current, **kw):
        seen.append((current["timestamp"], obs["exit_idx"].max() if len(obs) else -1, obs["entry_idx"].max() if len(obs) else -1))
        return real(name, obs, current, **kw)

    monkeypatch.setattr(er, "assess_observations", spy)
    decisions = evaluate(trades, feats, "BTC", warmup_frac=0.3)
    pos = {t: i for i, t in enumerate(feats["timestamp"])}
    assert decisions and all(ex <= pos[ts] and en < pos[ts] for ts, ex, en in seen)
    s = summarize(decisions)
    assert s["decision_points"] == len(decisions)


def test_bootstrap_ci_brackets_mean() -> None:
    v = np.random.default_rng(3).normal(0.2, 1, 300)
    lo, hi = bootstrap_ci(v, np.arange(300) // 10)
    assert lo < v.mean() < hi
