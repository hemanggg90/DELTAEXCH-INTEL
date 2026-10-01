"""
Historical analogue engine: a transparent k-NN.

The current market state is compared with the entry-time features of every past trade of a strategy, using a
standardised Euclidean distance over COMPARISON_FEATURES. The `top_k` nearest trades give a conditional expected net
R, win rate, median and confidence. Every analogue is inspectable (timestamp, features, outcome).

This is ported from the reference unchanged, apart from the day key: confidence and the standard error are based on
independent UTC EXCHANGE DAYS, not rows. Neighbouring bars of the same day are autocorrelated, so 30 analogues from 2
days carry roughly 2 observations of evidence, not 30.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COMPARISON_FEATURES = ["trend_slope", "momentum_20", "atr_percentile_100", "vol_expansion", "relative_volume",
                       "vwap_distance_pct"]
MIN_SAMPLE_FOR_ANY_CONFIDENCE = 10
MIN_DAYS_FOR_MEDIUM_CONFIDENCE = 6
MIN_DAYS_FOR_HIGH_CONFIDENCE = 15


def find_analogues(current_features: dict, observations: pd.DataFrame, top_k: int = 30) -> pd.DataFrame:
    """Top-k most similar observations with a `similarity_score` (0-1). Only features informative on both sides are
    used (finite now, at least 2 values and non-zero variance historically)."""
    if len(observations) == 0:
        return observations.assign(similarity_score=pd.Series(dtype=float))
    valid = []
    for c in COMPARISON_FEATURES:
        if c not in observations.columns:
            continue
        col = pd.to_numeric(observations[c], errors="coerce")
        q = current_features.get(c)
        try:
            q = float(q)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(q) or col.notna().sum() < 2 or not (col.std() > 0):
            continue
        valid.append(c)
    if not valid:
        return observations.head(0).assign(similarity_score=pd.Series(dtype=float))
    obs = observations.copy()
    for c in valid:
        obs[c] = pd.to_numeric(obs[c], errors="coerce")
        obs[c] = obs[c].fillna(obs[c].mean())
    means, stds = obs[valid].mean(), obs[valid].std()
    q = np.array([(float(current_features[c]) - means[c]) / stds[c] for c in valid])
    z = ((obs[valid] - means) / stds).to_numpy()
    d = np.sqrt(((z - q) ** 2).sum(axis=1))
    mx = d.max() if len(d) and d.max() > 0 else 1.0
    obs = obs.assign(similarity_score=1 - d / mx).sort_values("similarity_score", ascending=False, kind="stable")
    return obs.head(top_k)


def trading_days(analogues: pd.DataFrame) -> pd.Series | None:
    if "entry_timestamp" not in analogues.columns:
        return None
    return pd.to_datetime(analogues["entry_timestamp"], utc=True).dt.floor("D")


def cluster_robust_stderr(values: np.ndarray, days) -> float:
    """SE of the mean with observations clustered by day. With no cluster info it is the ordinary SE."""
    n = len(values)
    if n < 2:
        return np.nan
    resid = values - values.mean()
    if days is None:
        return float(values.std(ddof=1) / np.sqrt(n))
    sums = pd.Series(resid).groupby(np.asarray(days)).sum().to_numpy()
    g = len(sums)
    if g < 2:
        return np.nan
    return float(np.sqrt(g / (g - 1) * np.sum(sums ** 2)) / n)


def conditional_metrics_from_analogues(analogues: pd.DataFrame) -> dict:
    n = len(analogues)
    if n < MIN_SAMPLE_FOR_ANY_CONFIDENCE:
        return {"sample_size": n, "confidence_label": "INSUFFICIENT_DATA", "expected_r": None,
                "prob_positive_return": None, "win_rate": None, "median_r": None}
    r = analogues["r_multiple"].dropna()
    days = trading_days(analogues.loc[r.index])
    n_days = int(days.nunique()) if days is not None else n
    confidence = ("HIGH" if n_days >= MIN_DAYS_FOR_HIGH_CONFIDENCE else
                  "MEDIUM" if n_days >= MIN_DAYS_FOR_MEDIUM_CONFIDENCE else "LOW")
    se = cluster_robust_stderr(r.to_numpy(), days.to_numpy() if days is not None else None)
    return {"sample_size": n, "n_days": n_days, "confidence_label": confidence, "expected_r": float(r.mean()),
            "expected_r_stderr": float(se) if np.isfinite(se) else None,
            "prob_positive_return": float((r > 0).mean()), "win_rate": float((r > 0).mean()),
            "median_r": float(r.median())}
