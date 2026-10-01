from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.feature_engine import AuxData, compute_features
from delta_intelligence.strategies.base import apply_cooldown, first_per_day, research_frame
from delta_intelligence.strategies.library import OpeningRangeBreakout, SupertrendFlip
from delta_intelligence.strategies.registry import STRATEGY_CLASSES, get_all_strategies, get_strategy

UTC = dt.timezone.utc


def make_aux(ohlcv):
    rng = np.random.default_rng(5)
    hours = pd.date_range(ohlcv["timestamp"].iloc[0], ohlcv["timestamp"].iloc[-1], freq="1h")
    fund = np.where(rng.random(len(hours)) < 0.85, 0.01, rng.normal(0.01, 0.05, len(hours)))
    oi = 800 + np.cumsum(rng.normal(0, 4, len(hours)))
    mk = lambda v: pd.DataFrame({"timestamp": hours, "open": v, "high": v, "low": v, "close": v, "volume": np.nan})  # noqa: E731
    return AuxData(index=ohlcv.assign(close=ohlcv["close"] * 0.9996), funding=mk(fund), oi=mk(oi))


@pytest.fixture(scope="module")
def frame():
    o = synthetic_ohlcv(dt.datetime(2026, 8, 1, tzinfo=UTC), 288 * 20, seed=11)
    return research_frame(o, compute_features(o, "5m", make_aux(o)))


def test_registry_has_14_unique_backtestable_strategies() -> None:
    assert len(STRATEGY_CLASSES) == 14
    assert "Gap Fill Reversion" not in STRATEGY_CLASSES
    with pytest.raises(KeyError):
        get_strategy("Nope")


def test_every_strategy_produces_valid_setups(frame) -> None:
    for s in get_all_strategies():
        setups = s.historical_setups(frame)
        assert setups, f"{s.name} produced no setups on 20 days of synthetic data"
        for x in setups:
            if x.direction == "LONG":
                assert x.stop_price < x.entry_price < x.target_price, s.name
            else:
                assert x.target_price < x.entry_price < x.stop_price, s.name


@pytest.mark.parametrize("cut", [288 * 7 + 3, 288 * 12 + 140, 288 * 19])
def test_signals_have_no_lookahead(frame, cut) -> None:
    for s in get_all_strategies():
        full = s.signals(frame).iloc[:cut].reset_index(drop=True)
        part = s.signals(frame.iloc[:cut].reset_index(drop=True))
        pd.testing.assert_frame_equal(full, part, check_dtype=False, obj=s.name)


def test_live_check_equals_historical_setup(frame) -> None:
    """setup_now on data ending at a historical setup's bar returns exactly that setup (one code path)."""
    for s in get_all_strategies():
        setups = s.historical_setups(frame)
        pos = {t: i for i, t in enumerate(frame["timestamp"])}
        for x in setups[:: max(1, len(setups) // 5)]:
            live = s.setup_now(frame.iloc[: pos[x.timestamp] + 1])
            assert live is not None and live.direction == x.direction, s.name
            assert live.stop_price == pytest.approx(x.stop_price) and live.target_price == pytest.approx(x.target_price)


def test_supertrend_fires_only_on_flips(frame) -> None:
    sig = SupertrendFlip().signals(frame)
    flips = frame["supertrend_direction"].ne(frame["supertrend_direction"].shift(1)) & frame["supertrend_direction"].shift(1).notna()
    assert (sig["direction"] != 0).sum() <= flips.sum()
    assert (sig.loc[~flips, "direction"] == 0).all()


def test_orb_once_per_day_per_side_after_range(frame) -> None:
    setups = OpeningRangeBreakout().historical_setups(frame)
    day = [x.timestamp.floor("D") for x in setups]
    pairs = list(zip(day, [x.direction for x in setups]))
    assert len(pairs) == len(set(pairs))
    complete = frame.set_index("timestamp")["or_complete"]
    assert all(complete[x.timestamp] for x in setups)


def test_cooldown_and_first_per_day() -> None:
    d = np.array([1, 1, 0, -1, 1, 0, 0, 1, 1])
    assert list(apply_cooldown(d, 3)) == [1, 0, 0, -1, 0, 0, 0, 1, 0]  # kept at 0, 3, 7
    ev = pd.Series([False, True, True, False, True])
    day = pd.Series([1, 1, 1, 2, 2])
    assert list(first_per_day(ev, day)) == [False, True, False, False, True]


def test_volume_flags() -> None:
    names = {s.name for s in get_all_strategies() if s.uses_volume}
    assert names == {"Momentum", "VWAP Mean Reversion"}
