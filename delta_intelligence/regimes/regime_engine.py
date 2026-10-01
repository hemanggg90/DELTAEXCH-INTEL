"""
Regime engine: transparent, rule-based, no machine learning.

Each regime gets a score from already-computed features. The scores go through a temperature softmax, giving a
probability distribution rather than one hard label. The label is the argmax, and confidence is the max probability.
Both are always shown, so the UI never overstates certainty.

Regimes:
TREND_UP, TREND_DOWN, RANGE, COMPRESSION, VOL_EXPANSION, VOL_CONTRACTION, HIGH_VOL, LOW_VOL, EVENT_DRIVEN,
LIQUIDITY_STRESS.

Crypto adaptations vs the reference (whose event proxy was "near the Indian weekly expiry"):
- **EVENT_DRIVEN:** extreme funding (|funding_z_7d|), an open-interest shock (|oi_change_z_7d|), or a price shock
  (|return_1| large vs ATR). These are heuristics, not an event calendar.
- **LIQUIDITY_STRESS:** low relative volume with high volatility, a perp-vs-index basis dislocation, plus a small
  weekend weighting.
- **Trend strength is scale-free.** The reference used `tanh(slope * 500)`, tuned to NSE prices; on 60 days of BTC/ETH
  5m bars it never produced a TREND label (0%). The 20-bar regression move and the 20-bar momentum are now measured
  in ATR units: a move of ~3 ATRs over 20 bars counts as a clear trend. If ATR is missing, the old formula is used.
"""
from __future__ import annotations

import math

import numpy as np

REGIMES = [
    "TREND_UP", "TREND_DOWN", "RANGE", "COMPRESSION", "VOL_EXPANSION",
    "VOL_CONTRACTION", "HIGH_VOL", "LOW_VOL", "EVENT_DRIVEN", "LIQUIDITY_STRESS",
]
TEMPERATURE = 1.5


def _f(features: dict, key: str, default: float) -> float:
    val = features.get(key)
    try:
        val = float(val)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(val) else val


def regime_scores(features: dict) -> dict[str, float]:
    """Raw (pre-softmax) evidence per regime. Missing features fall back to neutral values."""
    slope = _f(features, "trend_slope", 0.0)
    momentum = _f(features, "momentum_20", 0.0)
    atr_pct = _f(features, "atr_percentile_100", 0.5)
    vol_exp = _f(features, "vol_expansion", 1.0)
    rel_vol = _f(features, "relative_volume", 1.0)
    structure = _f(features, "market_structure", 0.0)
    funding_z = abs(_f(features, "funding_z_7d", 0.0))
    oi_z = abs(_f(features, "oi_change_z_7d", 0.0))
    ret1 = abs(_f(features, "return_1", 0.0))
    atr_frac = _f(features, "atr_pct_of_price", 0.0)
    basis = abs(_f(features, "basis_pct", 0.0))
    weekend = 1.0 if features.get("is_weekend") is True else 0.0

    if atr_frac > 0:
        slope_atr = slope * 20 / atr_frac  # regression-line move over 20 bars, in ATRs
        momentum_atr = momentum / atr_frac  # close-to-close move over 20 bars, in ATRs
        trend = math.tanh(slope_atr / 3) * 0.6 + math.tanh(momentum_atr / 4) * 0.4
    else:
        trend = math.tanh(slope * 500) * 0.6 + math.tanh(momentum * 20) * 0.4
    shock = ret1 / atr_frac if atr_frac > 0 else 0.0

    s = {r: 0.0 for r in REGIMES}
    s["TREND_UP"] = max(trend, 0) * 3 + (1 if structure > 0 else 0)
    s["TREND_DOWN"] = max(-trend, 0) * 3 + (1 if structure < 0 else 0)
    s["RANGE"] = (1 - abs(trend)) * 2 + (1 if structure == 0 else 0)
    s["HIGH_VOL"] = atr_pct * 3
    s["LOW_VOL"] = (1 - atr_pct) * 3
    s["VOL_EXPANSION"] = max(vol_exp - 1, 0) * 4
    s["VOL_CONTRACTION"] = max(1 - vol_exp, 0) * 4
    s["COMPRESSION"] = (1 - atr_pct) * 2 + max(1 - vol_exp, 0) * 2
    s["EVENT_DRIVEN"] = max(0.0, funding_z - 1.5) * 1.5 + max(0.0, oi_z - 1.5) * 1.5 + max(0.0, shock - 3) * 1.0
    stress = (max(0.0, 1 - rel_vol) * 1.5 + atr_pct * 1.5) if rel_vol < 0.5 else 0.0
    s["LIQUIDITY_STRESS"] = stress + max(0.0, basis - 0.3) * 3 + weekend * 0.3
    return s


def classify_regime(features: dict) -> tuple[str, dict[str, float]]:
    """(label, probabilities over REGIMES)."""
    scores = regime_scores(features)
    values = np.array([scores[r] for r in REGIMES])
    e = np.exp((values - values.max()) / TEMPERATURE)
    probs = e / e.sum()
    prob = {r: float(p) for r, p in zip(REGIMES, probs)}
    return max(prob, key=prob.get), prob


def regime_confidence(probabilities: dict[str, float]) -> float:
    return max(probabilities.values()) if probabilities else 0.0


def classify_frame(features_df) -> "tuple[list[str], np.ndarray]":
    """Vectorised over rows (for the regime-history page and analogue research): labels and an (n, 10) prob array."""
    records = features_df.to_dict("records")
    labels, probs = [], np.zeros((len(records), len(REGIMES)))
    for i, row in enumerate(records):
        label, p = classify_regime(row)
        labels.append(label)
        probs[i] = [p[r] for r in REGIMES]
    return labels, probs
