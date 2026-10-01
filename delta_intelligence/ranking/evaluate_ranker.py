"""
Walk-forward evaluation of the strategy ranker itself: does it pick better trades than chance?

At every bar where at least one strategy opened an option trade, the ranker scores all strategies using ONLY trades
that had already CLOSED by that bar (exit_idx <= t and entry_idx < t). It runs through the same code as the live
pipeline (research/ranking_core.py). We then compare:
- the selected strategy's realised net option R at t (if it traded at t), with
- the mean net R of all strategies that traded at t (= picking one at random), and
- what NO TRADE avoided.

95% confidence intervals come from a bootstrap that resamples whole UTC days, because trades within a day are
correlated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_backtest import OptionTrade
from delta_intelligence.ranking.ranking_engine import rank_and_select
from delta_intelligence.research.ranking_core import assess_observations, observations_from_trades


@dataclass
class Decision:
    underlying: str
    timestamp: pd.Timestamp
    day: str
    selected: str | None
    n_signals: int
    selected_net_r: float | None
    pick_net_r: float


def evaluate(trades_by_strategy: dict[str, list[OptionTrade]], features: pd.DataFrame, underlying: str,
             warmup_frac: float = 0.4, max_points: int | None = None) -> list[Decision]:
    per = {}
    for name, trades in trades_by_strategy.items():
        if not trades:
            continue
        obs = observations_from_trades(trades, features, name)
        per[name] = {"obs": obs, "entry": obs["entry_idx"].to_numpy(), "exit": obs["exit_idx"].to_numpy(),
                     "r_at": dict(zip(obs["entry_idx"].astype(int), obs["r_multiple"]))}
    start = int(len(features) * warmup_frac)
    points = sorted({i for d in per.values() for i in d["r_at"] if i >= start})
    if max_points and len(points) > max_points:
        points = points[:: int(np.ceil(len(points) / max_points))]
    out: list[Decision] = []
    for t in points:
        current = features.iloc[t].to_dict()
        scores = [assess_observations(n, d["obs"][(d["exit"] <= t) & (d["entry"] < t)], current).score
                  for n, d in per.items()]
        ranking = rank_and_select(scores, "OK")
        at_t = {n: d["r_at"][t] for n, d in per.items() if t in d["r_at"]}
        sel = None if ranking.is_no_trade else ranking.selected_strategy
        ts = pd.Timestamp(features["timestamp"].iloc[t])
        out.append(Decision(underlying, ts, str(ts.date()), sel, len(at_t), at_t.get(sel) if sel else None,
                            float(np.mean(list(at_t.values())))))
    return out


def bootstrap_ci(values: np.ndarray, days: np.ndarray, n_boot: int = 2000, seed: int = 7):
    if len(values) == 0:
        return None
    rng = np.random.default_rng(seed)
    uniq = np.unique(days)
    by_day = {d: values[days == d] for d in uniq}
    means = [np.concatenate([by_day[d] for d in rng.choice(uniq, size=len(uniq))]).mean() for _ in range(n_boot)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarize(decisions: list[Decision]) -> dict:
    df = pd.DataFrame([asdict(d) for d in decisions])
    if df.empty:
        return {"decision_points": 0}
    traded = df[df["selected_net_r"].notna()]
    no_trade = df[df["selected"].isna()]
    out = {"decision_points": int(len(df)), "no_trade_points": int(len(no_trade)),
           "selected_but_no_setup": int((df["selected"].notna() & df["selected_net_r"].isna()).sum()),
           "trades_taken": int(len(traded)), "avg_signals_per_point": float(df["n_signals"].mean()),
           "take_every_signal_mean_net_r": float(df["pick_net_r"].mean())}
    if len(traded):
        sel, pick, days = traded["selected_net_r"].to_numpy(), traded["pick_net_r"].to_numpy(), traded["day"].to_numpy()
        out.update({"selected_mean_net_r": float(sel.mean()), "selected_win_rate": float((sel > 0).mean()),
                    "random_pick_mean_net_r": float(pick.mean()), "lift_vs_random_pick": float((sel - pick).mean()),
                    "selected_mean_net_r_ci95": bootstrap_ci(sel, days), "lift_ci95": bootstrap_ci(sel - pick, days),
                    "trade_days": int(len(np.unique(days)))})
    if len(no_trade):
        out["avoided_by_no_trade_mean_net_r"] = float(no_trade["pick_net_r"].mean())
    return out
