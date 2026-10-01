from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.data.data_manager import DataUnavailableError
from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.feature_engine import compute_features
from delta_intelligence.features.inputs import load_feature_inputs
from delta_intelligence.market_state.market_state_engine import build_current_state
from delta_intelligence.regimes.regime_engine import REGIMES, classify_frame, classify_regime, regime_confidence

BASE = {"trend_slope": 0.0, "momentum_20": 0.0, "atr_percentile_100": 0.5, "vol_expansion": 1.0,
        "relative_volume": 1.0, "market_structure": 0}


def test_probabilities_sum_to_one_over_ten_regimes() -> None:
    label, probs = classify_regime(BASE)
    assert len(REGIMES) == 10 and set(probs) == set(REGIMES) and label in REGIMES
    assert sum(probs.values()) == pytest.approx(1.0)


def test_uptrend_and_high_vol() -> None:
    assert classify_regime({**BASE, "trend_slope": 0.01, "momentum_20": 0.05, "market_structure": 1})[0] == "TREND_UP"
    _, p = classify_regime({**BASE, "atr_percentile_100": 0.98})
    assert p["HIGH_VOL"] > p["LOW_VOL"]


def test_trend_is_measured_in_atr_units() -> None:
    # 20-bar move of ~4 ATRs (slope*20/atr = 4.4) is a trend whatever the price scale.
    feats = {**BASE, "atr_pct_of_price": 0.001, "trend_slope": 0.00022, "momentum_20": 0.0045, "market_structure": 1}
    assert classify_regime(feats)[0] == "TREND_UP"
    down = {**feats, "trend_slope": -0.00022, "momentum_20": -0.0045, "market_structure": -1}
    assert classify_regime(down)[0] == "TREND_DOWN"
    # same raw slope but a much wider ATR (0.3 ATR move) is not a trend
    assert classify_regime({**feats, "atr_pct_of_price": 0.015, "market_structure": 0})[0] != "TREND_UP"


def test_missing_and_nan_features_do_not_crash() -> None:
    for feats in ({}, {k: float("nan") for k in BASE}, {"trend_slope": None, "is_weekend": "x"}):
        label, p = classify_regime(feats)
        assert label in REGIMES and sum(p.values()) == pytest.approx(1.0)


def test_funding_extreme_raises_event_driven() -> None:
    _, calm = classify_regime(BASE)
    _, hot = classify_regime({**BASE, "funding_z_7d": 4.5, "oi_change_z_7d": -3.0})
    assert hot["EVENT_DRIVEN"] > calm["EVENT_DRIVEN"] * 3


def test_basis_dislocation_raises_liquidity_stress() -> None:
    _, calm = classify_regime(BASE)
    _, stressed = classify_regime({**BASE, "basis_pct": 1.2})
    assert stressed["LIQUIDITY_STRESS"] > calm["LIQUIDITY_STRESS"]


def test_confidence_and_frame_classification() -> None:
    _, p = classify_regime({**BASE, "trend_slope": 0.01})
    assert regime_confidence(p) == max(p.values())
    f = compute_features(synthetic_ohlcv(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), 400))
    labels, probs = classify_frame(f)
    assert len(labels) == 400 and probs.shape == (400, 10)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0)


def test_market_state_warmup_and_regime() -> None:
    f = compute_features(synthetic_ohlcv(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), 400))
    assert build_current_state("BTCUSD", f.iloc[:100]) is None
    s = build_current_state("BTCUSD", f, "OK")
    assert s.regime in REGIMES and 0 < s.regime_confidence <= 1
    assert s.timestamp == f["timestamp"].iloc[-1].to_pydatetime()
    assert s.get("funding_rate", "n/a") == "n/a"  # NaN treated as missing


# ---- input loading ---------------------------------------------------------------------------------------------
class FakeDM:
    def __init__(self, fail: set[str], index_status: str = "OK"):
        self.fail = fail
        self.index_status = index_status
        self.df = synthetic_ohlcv(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), 400)

    def get_ohlcv(self, symbol, timeframe, start, end=None, **kw):
        key = "index" if symbol.startswith(".") else "perp"
        if key in self.fail:
            raise DataUnavailableError(f"{symbol} down")
        return self.df, {"quality_status": self.index_status if key == "index" else "OK"}

    def get_series(self, kind, symbol, timeframe, start, end=None, **kw):
        if kind.lower() in self.fail:
            raise DataUnavailableError(f"{kind} down")
        return self.df, {"quality_status": "OK"}


def test_inputs_best_effort_aux_and_index_downgrade() -> None:
    start = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    inp = load_feature_inputs(FakeDM(fail={"funding"}), "BTCUSD", "5m", start)
    assert inp.quality_status == "OK" and inp.aux.funding is None and "funding" in inp.aux_errors
    assert load_feature_inputs(FakeDM(fail={"index"}), "BTCUSD", "5m", start).quality_status == "DEGRADED"
    assert load_feature_inputs(FakeDM(fail=set(), index_status="FAIL"), "BTCUSD", "5m", start).quality_status == "DEGRADED"


def test_inputs_perp_missing_raises_and_unknown_symbol_rejected() -> None:
    start = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(DataUnavailableError):
        load_feature_inputs(FakeDM(fail={"perp"}), "BTCUSD", "5m", start)
    with pytest.raises(ValueError, match="no known option underlying"):
        load_feature_inputs(FakeDM(fail=set()), "SOLUSD", "5m", start)
