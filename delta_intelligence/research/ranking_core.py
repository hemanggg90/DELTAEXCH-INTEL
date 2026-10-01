"""
Shared ranking core: one place that turns a strategy's closed option trades plus the current market state into a
StrategyScore.

Used by the live pipeline AND by `ranking/evaluate_ranker.py`, so whatever is evaluated is exactly what runs live.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.analogues.analogue_engine import (
    COMPARISON_FEATURES,
    conditional_metrics_from_analogues,
    find_analogues,
)
from delta_intelligence.backtesting.option_backtest import OptionTrade, max_drawdown_r
from delta_intelligence.ranking.ranking_engine import StrategyScore, score_strategy


@dataclass
class Assessment:
    analogues: pd.DataFrame
    conditional: dict
    score: StrategyScore


def observations_from_trades(trades: list[OptionTrade], features: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    """One row per trade: entry-bar features + NET option R. `features` rows align with the backtest frame."""
    if not trades:
        return pd.DataFrame(columns=[*COMPARISON_FEATURES, "r_multiple", "entry_timestamp", "entry_idx", "exit_idx"])
    idx = np.array([t.entry_idx for t in trades])
    obs = features.iloc[idx][COMPARISON_FEATURES].reset_index(drop=True)
    obs["r_multiple"] = [t.net_r for t in trades]
    obs["underlying_r"] = [t.underlying_r for t in trades]
    obs["entry_timestamp"] = [t.entry_time for t in trades]
    obs["entry_idx"] = idx
    obs["exit_idx"] = [t.exit_idx for t in trades]
    obs["strategy_name"] = strategy_name
    return obs


def stability(observations: pd.DataFrame, n_folds: int = 4, min_trades: int = 3) -> dict:
    """Share of chronological folds with a positive mean net R (folds with too few trades are ignored)."""
    if len(observations) < n_folds * min_trades:
        return {"stability_folds": 0, "stability_positive_frac": None}
    ordered = observations.sort_values("entry_timestamp")["r_multiple"].to_numpy()
    means = [c.mean() for c in np.array_split(ordered, n_folds) if len(c) >= min_trades]
    if not means:
        return {"stability_folds": 0, "stability_positive_frac": None}
    return {"stability_folds": len(means), "stability_positive_frac": float(np.mean([m > 0 for m in means]))}


def assess_observations(strategy_name: str, observations: pd.DataFrame, current_features: dict,
                        top_k: int = 30) -> Assessment:
    analogues = find_analogues(current_features, observations, top_k=top_k)
    conditional = conditional_metrics_from_analogues(analogues)
    conditional["strategy_name"] = strategy_name
    conditional.update(stability(observations))
    if len(observations):
        conditional["global_expected_r"] = float(observations["r_multiple"].mean())
    dd = max_drawdown_r(observations.sort_values("entry_timestamp")["r_multiple"].to_numpy()) if len(observations) else None
    score = score_strategy(conditional, dd)
    return Assessment(analogues=analogues, conditional=conditional, score=score)
