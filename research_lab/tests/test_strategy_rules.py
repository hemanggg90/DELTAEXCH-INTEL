"""Each strategy's rule, on hand-built frames: it fires when the hypothesis is met, stays silent when a required
condition is missing, never looks ahead, and refuses invalid input."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from helpers import mini_frame, with_close  # noqa: E402
from lab import config as C
from lab.strategies import (DonchianBreakout, EMATrendADX, FundingOIDivergence, LongStraddleExpansion,
                            MeanReversionExtreme, OpeningRangeBreakout, SupertrendVolFilter, SweepMSS, VolBreakoutTrend,
                            VWAPMomentumBreakout)


def dirs(strategy, f):
    return strategy.signals(f)["direction"].to_numpy()


# ---- S1 --------------------------------------------------------------------------------------------------------------
def test_s1_requires_compression_trend_and_breakout() -> None:
    n = 120
    close = np.full(n, 100.0)
    close[80:] = 103.0  # a clear break above the previous 20-bar range at bar 80
    bbw = np.full(n, 0.5)
    bbw[60:75] = 0.05  # compression shortly before the break
    f = with_close(mini_frame(n, bbw_pct=bbw, ema_20=close - 0.0, ema_50=np.full(n, 99.0)), close)
    f["ema_20"] = 101.0  # EMA20 > EMA50, +DI > -DI, ADX 30 (mini_frame defaults)
    s = VolBreakoutTrend(C.VolBreakoutConfig(range_n=20, compress_window=30, cooldown_bars=1))
    d = dirs(s, f)
    assert d[80] == 1 and d[81:].sum() == 0  # first breakout bar only
    # no recent compression -> no signal
    assert dirs(s, f.assign(bbw_pct=0.5)).sum() == 0
    # weak trend -> no signal
    assert dirs(s, f.assign(adx_14=10.0)).sum() == 0
    # already-extended volatility is skipped unless the continuation filter passes
    ext = f.assign(atr_pct=0.99, relative_volume=0.5)
    assert dirs(s, ext).sum() == 0
    cont = f.assign(atr_pct=0.99, relative_volume=2.0)  # breakout is 3 ATR beyond the range, volume strong
    assert dirs(s, cont)[80] == 1


def test_s1_downside_buys_put() -> None:
    n = 120
    close = np.full(n, 100.0)
    close[80:] = 97.0
    bbw = np.full(n, 0.5)
    bbw[60:75] = 0.05
    f = with_close(mini_frame(n, bbw_pct=bbw), close)
    f["ema_20"], f["ema_50"], f["plus_di_14"], f["minus_di_14"] = 99.0, 101.0, 10.0, 30.0
    s = VolBreakoutTrend(C.VolBreakoutConfig(compress_window=30))
    assert dirs(s, f)[80] == -1


# ---- S2 --------------------------------------------------------------------------------------------------------------
def test_s2_one_signal_per_breakout_and_rearms() -> None:
    n = 80
    close = np.full(n, 100.0)
    close[40:50] = 104.0  # breakout above the 20-bar channel, stays outside for 10 bars
    close[50:60] = 100.0  # back inside
    close[60:] = 108.0  # a second breakout, beyond the earlier highs still inside the 20-bar window
    f = with_close(mini_frame(n), close)
    d = dirs(DonchianBreakout(C.DonchianConfig(n=20, cooldown_bars=1)), f)
    assert np.flatnonzero(d == 1).tolist() == [40, 60]  # not 40..49: no duplicate entries inside one breakout


def test_s2_filters() -> None:
    n = 80
    close = np.full(n, 100.0)
    close[40:] = 104.0
    f = with_close(mini_frame(n, relative_volume=0.5), close)
    plain = DonchianBreakout(C.DonchianConfig(n=20))
    assert dirs(plain, f)[40] == 1
    assert dirs(DonchianBreakout(C.DonchianConfig(n=20, min_rel_vol=1.0)), f).sum() == 0
    # trend filter: EMA50 below price and rising passes; falling fails
    rising = f.assign(ema_50=np.linspace(90, 100, n))
    falling = f.assign(ema_50=np.linspace(110, 100, n))
    tf = DonchianBreakout(C.DonchianConfig(n=20, trend_filter=1))
    assert dirs(tf, rising)[40] == 1 and dirs(tf, falling).sum() == 0


# ---- S3 --------------------------------------------------------------------------------------------------------------
def test_s3_vwap_displacement_momentum_and_anti_chase() -> None:
    n = 200
    close = np.full(n, 100.0)
    close[150:] = 102.0  # 2 ATR above VWAP (100), momentum positive
    f = with_close(mini_frame(n, start="2026-03-02T00:00Z"), close)
    f["minutes_into_day"] = np.arange(n) * 5.0 + 240  # past the warm-up
    s = VWAPMomentumBreakout(C.VWAPMomentumConfig(cooldown_bars=1))
    assert dirs(s, f)[150] == 1
    chased = close.copy()
    chased[150:] = 106.0  # 6 ATR from VWAP: exhausted
    assert dirs(s, with_close(f, chased)).sum() == 0
    assert dirs(s, f.assign(minutes_into_day=30.0)).sum() == 0  # before the VWAP warm-up
    assert dirs(s, f.assign(relative_volume=0.2)).sum() == 0
    below = close.copy()
    below[150:] = 98.0
    assert dirs(s, with_close(f, below))[150] == -1


# ---- S4 --------------------------------------------------------------------------------------------------------------
def _sweep_frame(bull: bool) -> pd.DataFrame:
    """Zig-zag with fractal swings (n=2), then a sweep of the last swing and a break of structure."""
    pts = [100, 102, 104, 102, 100, 98, 96, 98, 100, 101, 102, 100, 99, 100, 101, 103, 105, 106]
    sweep_i = 12  # a dip below the swing low (96)
    path = np.array(pts, float)
    path = np.concatenate([path, np.full(30, 106.0)])
    f = with_close(mini_frame(len(path)), path, wick=0.3)
    f.loc[sweep_i, "low"] = 95.0
    f.loc[sweep_i, "close"] = 99.0  # wick below 96, closes back above
    return f if bull else None


def test_s4_bullish_sweep_then_structure_shift_buys_call() -> None:
    f = _sweep_frame(True)
    s = SweepMSS(C.SweepMSSConfig(swing_n=2, sweep_thr_atr=0.1, confirm_window=8, cooldown_bars=1))
    d = dirs(s, f)
    longs = np.flatnonzero(d == 1)
    assert len(longs) >= 1 and longs[0] > 12  # strictly AFTER the sweep bar
    sig = s.signals(f)
    i = longs[0]
    assert sig["stop"].iloc[i] < f["close"].iloc[i] < sig["target"].iloc[i]


def test_s4_no_signal_without_sweep_or_after_window() -> None:
    f = _sweep_frame(True)
    f.loc[12, ["low", "close"]] = [99.5, 100.0]  # no wick beyond the swing low
    s = SweepMSS(C.SweepMSSConfig(swing_n=2, confirm_window=8))
    assert (dirs(s, f) == 1).sum() == 0
    g = _sweep_frame(True)
    tight = SweepMSS(C.SweepMSSConfig(swing_n=2, confirm_window=1))  # shift comes later than 1 bar
    assert (dirs(tight, g) == 1).sum() == 0


# ---- S5 --------------------------------------------------------------------------------------------------------------
def _div_frame(fz, oz, up=True):
    n = 60
    close = np.full(n, 100.0)
    close[40:] = 104.0 if up else 96.0
    f = with_close(mini_frame(n, funding_z_7d=fz, oi_change_z_7d=oz), close)
    return f


def test_s5_fade_rule_and_confirmation() -> None:
    n = 60
    close = np.full(n, 100.0)
    close[45:50] = 104.0  # a fresh rally of 4 ATR within the 12-bar lookback
    close[50] = 103.0  # confirmation bar closes down
    f = with_close(mini_frame(n, funding_z_7d=2.0, oi_change_z_7d=-2.0), close)
    s = FundingOIDivergence(C.FundingOIConfig(mode=0, lookback=12, cooldown_bars=1))
    d = dirs(s, f)
    assert (d == -1).any()  # rally + falling OI + crowded-long funding + bearish bar -> buy a put
    assert (d == 1).sum() == 0
    assert dirs(s, f.assign(oi_change_z_7d=0.0)).sum() == 0  # positioning not weakening: no signal


def test_s5_follow_rule_is_a_different_hypothesis() -> None:
    n = 60
    close = np.full(n, 100.0)
    close[30:] = np.linspace(101, 106, n - 30)
    f = with_close(mini_frame(n, funding_z_7d=0.0, oi_change_z_7d=2.0), close)
    follow = FundingOIDivergence(C.FundingOIConfig(mode=1, cooldown_bars=1))
    fade = FundingOIDivergence(C.FundingOIConfig(mode=0, cooldown_bars=1))
    assert (dirs(follow, f) == 1).any()  # building OI with the move, funding not crowded -> call
    assert dirs(fade, f).sum() == 0


def test_s5_missing_funding_or_oi_is_no_signal_not_invented() -> None:
    f = _div_frame(np.nan, -2.0)
    s = FundingOIDivergence(C.FundingOIConfig(mode=0, cooldown_bars=1))
    assert dirs(s, f).sum() == 0
    out = s.evaluate_now(f, "BTC")
    assert out.kind == "NO_SIGNAL" and "funding_z_7d" in out.reason
    assert dirs(s, _div_frame(2.0, np.nan)).sum() == 0
    no_cols = _div_frame(2.0, -2.0).drop(columns=["funding_z_7d"])
    assert s.evaluate_now(no_cols, "BTC").kind == "NO_SIGNAL"


# ---- S6 --------------------------------------------------------------------------------------------------------------
def test_s6_needs_enough_compression_measures_and_is_a_straddle() -> None:
    n = 100
    low = np.full(n, 0.9)
    low[50:] = 0.05
    f = mini_frame(n, rv_pct=low, atr_pct=low, bbw_pct=np.full(n, 0.9))
    s = LongStraddleExpansion(C.StraddleConfig(min_conditions=2, cooldown_bars=1))
    d = dirs(s, f)
    assert s.policy == "LONG_STRADDLE" and np.flatnonzero(d == 2).tolist() == [50]  # first bar of the condition only
    assert dirs(LongStraddleExpansion(C.StraddleConfig(min_conditions=3, cooldown_bars=1)), f).sum() == 0
    setups = s.historical_setups(f)
    assert len(setups) == 1 and setups[0].direction == "VOL"
    assert setups[0].meta["expected_abs_move"] > 0 and np.isnan(s.signals(f)["stop"].iloc[50])


def test_s6_iv_filter_applies_only_when_iv_exists() -> None:
    n = 100
    low = np.full(n, 0.9)
    low[50:] = 0.05
    base = mini_frame(n, rv_pct=low, atr_pct=low, bbw_pct=low)
    s = LongStraddleExpansion(C.StraddleConfig(iv_pct_max=40.0, cooldown_bars=1))
    assert dirs(s, base).sum() > 0  # no IV history -> filter ignored (IV is NaN in mini_frame)
    assert dirs(s, base.assign(iv_percentile=90.0)).sum() == 0  # expensive options -> skipped
    assert dirs(s, base.assign(iv_percentile=10.0)).sum() > 0


# ---- S7 --------------------------------------------------------------------------------------------------------------
def _orb_frame(session_start="2026-03-02T13:30Z", breakout_bars=(), hours=8):
    n = hours * 12 + 12
    f = mini_frame(n, start=pd.Timestamp(session_start).isoformat())
    return f


def test_s7_breakout_after_range_once_per_side_with_reset_option() -> None:
    n = 90
    close = np.full(n, 100.0)
    close[8:12] = 103.0  # first breakout above the 30-min range (bars 0-5)
    close[12:14] = 100.0  # back inside
    close[14:18] = 103.0  # second breakout, same range and side
    f = with_close(mini_frame(n, start="2026-03-02T13:30Z"), close, wick=0.4)
    cfg = dict(session=1, range_min=30, cooldown_bars=1)
    once = OpeningRangeBreakout(C.ORBConfig(**cfg, reset_inside=0))
    reset = OpeningRangeBreakout(C.ORBConfig(**cfg, reset_inside=1))
    assert np.flatnonzero(dirs(once, f) == 1).tolist() == [8]
    assert np.flatnonzero(dirs(reset, f) == 1).tolist() == [8, 14]
    sig = once.signals(f)
    assert sig["stop"].iloc[8] < 103.0 < sig["target"].iloc[8]  # stop = middle of the range


def test_s7_ignores_bars_before_the_range_completes_and_after_the_window() -> None:
    n = 90
    close = np.full(n, 100.0)
    close[3:6] = 103.0  # inside the range window itself: not a breakout
    f = with_close(mini_frame(n, start="2026-03-02T13:30Z"), close, wick=0.4)
    assert dirs(OpeningRangeBreakout(C.ORBConfig(session=1, cooldown_bars=1)), f).sum() == 0
    late = np.full(n, 100.0)
    late[80:] = 103.0  # > 4 h after the range: outside the signal window
    g = with_close(mini_frame(n, start="2026-03-02T13:30Z"), late, wick=0.4)
    assert dirs(OpeningRangeBreakout(C.ORBConfig(session=1, window_hours=1.0, cooldown_bars=1)), g).sum() == 0


def test_s7_partial_range_after_a_data_gap_is_not_used() -> None:
    n = 60
    close = np.full(n, 100.0)
    close[20:] = 103.0
    f = with_close(mini_frame(n, start="2026-03-02T13:30Z"), close, wick=0.4)
    f = f.drop(index=[1, 2]).reset_index(drop=True)  # two range bars missing
    assert dirs(OpeningRangeBreakout(C.ORBConfig(session=1, cooldown_bars=1)), f).sum() == 0


# ---- S8 --------------------------------------------------------------------------------------------------------------
def test_s8_first_aligned_bar_only_and_adx_floor() -> None:
    n = 400
    close = np.concatenate([np.full(100, 100.0), np.linspace(100, 160, n - 100)])
    f = with_close(mini_frame(n), close)
    s = EMATrendADX(C.EMAADXConfig(cooldown_bars=1))
    d = dirs(s, f)
    longs = np.flatnonzero(d == 1)
    assert len(longs) >= 1 and longs[0] > 100 and (np.diff(longs) > 1).all()  # no stacking on consecutive bars
    assert dirs(s, f.assign(adx_14=10.0)).sum() == 0  # low trend strength: never traded
    falling = with_close(mini_frame(n, plus_di_14=10.0, minus_di_14=30.0), close[::-1].copy())
    assert (dirs(s, falling) == -1).any() and (dirs(s, falling) == 1).sum() == 0


# ---- S9 --------------------------------------------------------------------------------------------------------------
def _st_frame(n=300):
    t = np.arange(n)
    close = 100 + 10 * np.sin(t / 15.0)
    return with_close(mini_frame(n), close, wick=0.5)


def test_s9_filter_variants_and_control() -> None:
    f = _st_frame()
    control = SupertrendVolFilter(C.SupertrendVolConfig(filter_kind=0, cooldown_bars=1))
    n_control = int((dirs(control, f) != 0).sum())
    assert n_control >= 2  # the oscillation flips direction several times
    never = SupertrendVolFilter(C.SupertrendVolConfig(filter_kind=1, atr_lo=0.0, atr_hi=0.1, cooldown_bars=1))
    assert (dirs(never, f.assign(atr_pct=0.9)) != 0).sum() == 0  # filter rejects every flip
    adx = SupertrendVolFilter(C.SupertrendVolConfig(filter_kind=2, adx_min=20.0, cooldown_bars=1))
    assert (dirs(adx, f.assign(adx_14=5.0)) != 0).sum() == 0
    assert (dirs(adx, f.assign(adx_14=50.0)) != 0).sum() == n_control  # same flips when the filter passes
    sig = control.signals(f)
    i = int(np.flatnonzero(sig["direction"].to_numpy())[0])
    if sig["direction"].iloc[i] == 1:  # stop is the supertrend line, target 2.25 ATR away
        assert sig["target"].iloc[i] == pytest.approx(f["close"].iloc[i] + 2.25 * f["atr_14"].iloc[i])


# ---- S10 -------------------------------------------------------------------------------------------------------------
def _mr_frame():
    n = 120
    close = np.full(n, 100.0)
    close[60:63] = [97.0, 96.0, 96.4]  # sell-off to 4 ATR below fair, then a bounce bar
    f = with_close(mini_frame(n), close, wick=0.1)
    f.loc[62, ["open", "high", "low", "close"]] = [96.0, 97.0, 95.2, 96.9]  # bullish, long lower wick, recovers 1.7 ATR
    f["minutes_into_day"] = np.arange(n) * 5.0 + 600
    return f


def test_s10_extreme_plus_exhaustion_buys_the_opposite_option() -> None:
    f = _mr_frame()
    s = MeanReversionExtreme(C.MeanRevConfig(fair=1, z_min=2.5, cooldown_bars=1))
    d = dirs(s, f)
    assert d[62] == 1 and (d != 0).sum() == 1
    sig = s.signals(f)
    assert sig["target"].iloc[62] == pytest.approx(100.0)  # reversion to fair value
    assert sig["stop"].iloc[62] < 95.2


def test_s10_no_trade_on_oversold_alone() -> None:
    f = _mr_frame()
    s = MeanReversionExtreme(C.MeanRevConfig(fair=1, cooldown_bars=1))
    no_bounce = f.copy()
    no_bounce.loc[62, ["open", "high", "low", "close"]] = [96.5, 96.6, 95.5, 95.6]  # still falling: no rejection
    assert dirs(s, no_bounce).sum() == 0
    assert dirs(s, f.assign(vol_expansion=3.0)).sum() == 0  # volatility exploding: skipped
    assert dirs(MeanReversionExtreme(C.MeanRevConfig(fair=1, z_min=5.0, cooldown_bars=1)), f).sum() == 0
    assert dirs(s, f.assign(atr_pct=0.99)).sum() == 0


# ---- config validation -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cls,bad", [
    (C.VolBreakoutConfig, dict(squeeze_pct=0.0)), (C.VolBreakoutConfig, dict(range_n=2)),
    (C.DonchianConfig, dict(n=1)), (C.DonchianConfig, dict(trend_filter=2)),
    (C.VWAPMomentumConfig, dict(disp_min_atr=3.0, max_dist_atr=2.0)),
    (C.SweepMSSConfig, dict(swing_n=1)), (C.SweepMSSConfig, dict(confirm_window=0)),
    (C.FundingOIConfig, dict(mode=5)), (C.FundingOIConfig, dict(z_oi=-1.0)),
    (C.StraddleConfig, dict(min_conditions=4)), (C.StraddleConfig, dict(rv_pct_max=1.5)),
    (C.ORBConfig, dict(session=9)), (C.ORBConfig, dict(min_range_atr=5.0, max_range_atr=1.0)),
    (C.EMAADXConfig, dict(fast=50, slow=20)), (C.SupertrendVolConfig, dict(filter_kind=7)),
    (C.SupertrendVolConfig, dict(atr_lo=0.9, atr_hi=0.5)), (C.MeanRevConfig, dict(fair=3)),
    (C.MeanRevConfig, dict(wick_frac=1.5)), (C.BaseConfig, dict(rr=0)),
    (C.BaseConfig, dict(hold_hours=10.0, max_hold_hours=5.0)), (C.BaseConfig, dict(cooldown_bars=0)),
])
def test_invalid_parameters_are_rejected(cls, bad) -> None:
    with pytest.raises(C.ConfigError):
        cls(**bad)


def test_configs_are_frozen_and_changeable_without_code() -> None:
    c = C.DonchianConfig()
    with pytest.raises(Exception):
        c.n = 5  # type: ignore[misc]
    assert c.with_(n=40).n == 40 and c.n == 20
    assert DonchianBreakout(C.DonchianConfig(n=40)).parameters["n"] == 40


def test_strategy_rejects_the_wrong_config_type() -> None:
    with pytest.raises(TypeError):
        DonchianBreakout(C.EMAADXConfig())
