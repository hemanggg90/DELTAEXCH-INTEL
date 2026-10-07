"""The production copy of S1-S10 (delta_intelligence.strategies.lab_library) must produce exactly the same signals as the
frozen research copy (research_lab/lab). If either drifts, this fails."""
from __future__ import annotations

import numpy as np
import pytest

from delta_intelligence.features.feature_engine import compute_features
from delta_intelligence.strategies.lab_library.columns import LAB_COLUMNS
from delta_intelligence.strategies.universe import LAB_BASELINES, build_universe_frame
from lab.frame import build_lab_frame
from lab.library import variant_by_id
from synth import iv_hourly, make_inputs

BASELINE_IDS = {"S1 Volatility Breakout + Trend": "S1-base", "S2 Donchian Breakout": "S2-n20", "S3 VWAP + Momentum Breakout": "S3-base",
                "S4 Liquidity Sweep + Market Structure Shift": "S4-base", "S5 Funding/OI + Price Divergence": "S5-fade",
                "S6 Long Straddle Volatility Expansion": "S6-base", "S7 Opening Range Breakout": "S7-us-open",
                "S8 EMA Trend + ADX": "S8-20-50", "S9 Supertrend + Volatility Filter": "S9-atr-band", "S10 Mean-Reversion Extreme": "S10-vwap"}


@pytest.fixture(scope="module")
def frames():
    o, aux = make_inputs(days=50, seed=44)
    iv = iv_hourly(o)
    research = build_lab_frame(o, aux, "5m", iv, with_regime=False)
    production = build_universe_frame(o, compute_features(o, "5m", aux), iv, None)
    return research, production


def test_production_frame_has_the_same_extra_columns_with_the_same_values(frames) -> None:
    research, production = frames
    assert len(research) == len(production)
    for c in LAB_COLUMNS:
        assert np.allclose(research[c].to_numpy(float), production[c].to_numpy(float), equal_nan=True), c


@pytest.mark.parametrize("cls,cfg", LAB_BASELINES, ids=[c.name for c, _ in LAB_BASELINES])
def test_ported_strategy_gives_identical_signals(frames, cls, cfg) -> None:
    research, production = frames
    ref = variant_by_id(BASELINE_IDS[cls.name]).build("5m")
    got = cls(cfg, "5m")
    assert got.name == ref.name and got.config.as_dict() == ref.config.as_dict()
    a, b = ref.signals(research), got.signals(production)
    assert (a["direction"].to_numpy() == b["direction"].to_numpy()).all()
    assert np.allclose(a["stop"].to_numpy(float), b["stop"].to_numpy(float), equal_nan=True)
    assert np.allclose(a["target"].to_numpy(float), b["target"].to_numpy(float), equal_nan=True)
    assert (a["direction"] != 0).sum() == (b["direction"] != 0).sum()
    assert ref.expected_hold_bars == got.expected_hold_bars and ref.max_hold_bars == got.max_hold_bars and ref.policy == got.policy
