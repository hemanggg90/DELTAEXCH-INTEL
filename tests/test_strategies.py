from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.config.events import load_events
from delta_intelligence.data_adapters.synthetic import synthetic_ohlcv
from delta_intelligence.features.feature_engine import AuxData, compute_features
from delta_intelligence.strategies.base import apply_cooldown, first_per_day
from delta_intelligence.strategies.context import build_strategy_frame
from delta_intelligence.strategies.library_v3 import PreEventStraddle
from delta_intelligence.strategies.registry import STRATEGY_CLASSES, get_all_strategies, get_strategy

UTC = dt.timezone.utc


@pytest.fixture(scope="module")
def frame():
    o = synthetic_ohlcv(dt.datetime(2026, 1, 5, tzinfo=UTC), 288 * 75, seed=13)
    hours = pd.date_range(o["timestamp"].iloc[0], o["timestamp"].iloc[-1], freq="1h")
    rng = np.random.default_rng(9)
    mk = lambda v: pd.DataFrame({"timestamp": hours, "open": v, "high": v, "low": v, "close": v, "volume": np.nan})  # noqa: E731
    aux = AuxData(index=o.assign(close=o["close"] * 0.9996), funding=mk(rng.normal(0.01, 0.01, len(hours))),
                  oi=mk(800 + np.cumsum(rng.normal(0, 8, len(hours)))))
    h = pd.date_range(o["timestamp"].iloc[0] - pd.Timedelta(days=61), periods=24 * 140, freq="1h")
    iv = pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"),
                       "atm_iv_6_30h": 0.4 + 0.15 * np.sin(np.arange(len(h)) / 50)})
    events = pd.DataFrame({"timestamp_utc": pd.date_range("2026-01-20T19:00Z", periods=10, freq="5D"),
                           "name": "EVT", "importance": "high"})
    return build_strategy_frame(o, compute_features(o, "5m", aux), iv, events)


def test_registry_is_the_v3_library() -> None:
    assert len(STRATEGY_CLASSES) == 10
    assert not any(s.family == "reversion" for s in get_all_strategies())  # no mean-reversion-in-range
    for s in get_all_strategies():
        assert set(s.key_parameters) <= set(s.default_parameters), s.name
        assert s.max_hold_bars >= s.expected_hold_bars > 0
    with pytest.raises(KeyError):
        get_strategy("Momentum")


def test_most_strategies_fire_and_geometry_is_valid(frame) -> None:
    fired = 0
    for s in get_all_strategies():
        setups = s.historical_setups(frame)
        fired += bool(setups)
        for x in setups:
            if x.direction == "LONG":
                assert x.stop_price < x.entry_price < x.target_price, s.name
            elif x.direction == "SHORT":
                assert x.target_price < x.entry_price < x.stop_price, s.name
            else:
                assert x.direction == "VOL" and x.meta["expected_abs_move"] > 0
    assert fired >= 8


@pytest.mark.parametrize("cut", [288 * 40 + 9, 288 * 58 + 200, 288 * 74])
def test_signals_have_no_lookahead(frame, cut) -> None:
    for s in get_all_strategies():
        full = s.signals(frame).iloc[:cut].reset_index(drop=True)
        part = s.signals(frame.iloc[:cut].reset_index(drop=True))
        pd.testing.assert_frame_equal(full, part, check_dtype=False, obj=s.name)


def test_live_check_equals_historical(frame) -> None:
    pos = {t: i for i, t in enumerate(frame["timestamp"])}
    for s in get_all_strategies():
        setups = s.historical_setups(frame)
        for x in setups[:: max(1, len(setups) // 4)]:
            live = s.setup_now(frame.iloc[: pos[x.timestamp] + 1])
            assert live is not None and live.direction == x.direction, s.name


def test_event_strategy_needs_events_and_low_iv(frame) -> None:
    s = PreEventStraddle()
    setups = s.historical_setups(frame)
    assert setups and all(x.direction == "VOL" for x in setups)
    assert len({x.meta["event"] for x in setups}) == 1 and len(setups) <= 10  # at most one per event
    no_events = frame.assign(hours_to_event=np.nan, event_name=None)
    assert s.historical_setups(no_events) == []


def test_events_file_loader(tmp_path) -> None:
    assert len(load_events(tmp_path / "missing.csv")) == 0
    p = tmp_path / "e.csv"
    p.write_text("timestamp_utc,name,importance\n2026-11-04T19:00:00Z,FOMC,high\n", encoding="utf-8")
    ev = load_events(p)
    assert ev["timestamp_utc"].iloc[0] == pd.Timestamp("2026-11-04T19:00Z")
    p.write_text("timestamp_utc,name,importance\n2026-11-04 19:00,FOMC,high\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_events(p)


def test_cooldown_and_first_per_day() -> None:
    assert list(apply_cooldown(np.array([1, 1, 0, -1, 1, 0, 0, 1, 1]), 3)) == [1, 0, 0, -1, 0, 0, 0, 1, 0]
    assert list(first_per_day(pd.Series([False, True, True, False, True]), pd.Series([1, 1, 1, 2, 2]))) == \
        [False, True, False, False, True]
