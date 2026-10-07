"""The 10 long-option strategy families. Each one is a hypothesis with explicit, measurable conditions.

Rules for every strategy here:
- signals use closed bars only (row i reads rows <= i; the test-suite proves it by truncation);
- warm-up or missing inputs mean NO SIGNAL, never an invented value;
- only LONG options are ever implied: a call (long view), a put (short view) or a call+put straddle;
- the underlying stop and target are proposals; the project's selector, breakeven gate, premium stop, expiry guard and
  risk engine decide what actually happens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.features.feature_engine import _supertrend  # existing implementation, reused
from delta_intelligence.strategies import primitives as P
from delta_intelligence.strategies.lab_library.base import Decision, LabStrategy, clip01
from delta_intelligence.strategies.lab_library.config import (SESSIONS, DonchianConfig, EMAADXConfig, FundingOIConfig, MeanRevConfig, ORBConfig,
                        StraddleConfig, SupertrendVolConfig, SweepMSSConfig, VolBreakoutConfig, VWAPMomentumConfig)
from delta_intelligence.strategies.lab_library.indicators import donchian, rising_edge


def _stop_atr(f: pd.DataFrame, long_: pd.Series, short: pd.Series, mult: float) -> pd.Series:
    return pd.Series(np.where(long_, f["close"] - mult * f["atr_14"], f["close"] + mult * f["atr_14"]), index=f.index)


class VolBreakoutTrend(LabStrategy):
    name = "S1 Volatility Breakout + Trend"
    family = "breakout"
    config_cls = VolBreakoutConfig
    required = ("atr_14", "bbw_pct", "atr_pct", "adx_14", "plus_di_14", "minus_di_14", "ema_20", "ema_50",
                "relative_volume")
    entry_text = ("Bollinger-width percentile <= {c.squeeze_pct} within the previous {c.compress_window} bars, then a "
                  "close beyond the previous {c.range_n}-bar range, with EMA20/EMA50, +DI/-DI and ADX >= {c.adx_min} "
                  "agreeing. Skipped when ATR percentile > {c.ext_atr_pct} unless the breakout is >= {c.cont_atr} ATR "
                  "beyond the range on relative volume >= {c.cont_rel_vol}.")
    invalidation_text = "Close back inside the broken range (the underlying stop is placed beyond it)."
    stop_text = "close -/+ {c.stop_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        hi, lo = donchian(f["high"], f["low"], int(c.range_n))
        squeezed = f["bbw_pct"].shift(1).rolling(int(c.compress_window), min_periods=1).min() <= c.squeeze_pct
        up = (f["close"] > hi) & (f["ema_20"] > f["ema_50"]) & (f["plus_di_14"] > f["minus_di_14"])
        dn = (f["close"] < lo) & (f["ema_20"] < f["ema_50"]) & (f["minus_di_14"] > f["plus_di_14"])
        trend = f["adx_14"] >= c.adx_min
        beyond = pd.concat([f["close"] - hi, lo - f["close"]], axis=1).max(axis=1) / f["atr_14"]
        extended = f["atr_pct"] > c.ext_atr_pct
        cont = (beyond >= c.cont_atr) & (f["relative_volume"] >= c.cont_rel_vol)
        gate = squeezed & trend & (~extended | cont)
        long_, short = rising_edge(up & gate), rising_edge(dn & gate)
        strength = clip01(0.5 * clip01((f["adx_14"] - c.adx_min) / 20) + 0.5 * clip01(beyond))
        return Decision(long_, short, _stop_atr(f, long_, short, c.stop_atr), strength=strength)


class DonchianBreakout(LabStrategy):
    name = "S2 Donchian Breakout"
    family = "breakout"
    config_cls = DonchianConfig
    required = ("atr_14", "atr_pct", "ema_50", "relative_volume")
    entry_text = ("Confirmed close outside the previous {c.n}-bar channel (first bar of each breakout only). Optional "
                  "filters: EMA50 slope agreeing (trend_filter={c.trend_filter}), relative volume >= {c.min_rel_vol}, "
                  "ATR percentile <= {c.atr_pct_max}.")
    invalidation_text = "Close back inside the channel (a new breakout can then signal again)."
    stop_text = "close -/+ {c.stop_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        hi, lo = donchian(f["high"], f["low"], int(c.n))
        up_out, dn_out = f["close"] > hi, f["close"] < lo
        slope_up = f["ema_50"] > f["ema_50"].shift(5)
        up_ok = (f["close"] > f["ema_50"]) & slope_up if c.trend_filter else pd.Series(True, index=f.index)
        dn_ok = (f["close"] < f["ema_50"]) & ~slope_up if c.trend_filter else pd.Series(True, index=f.index)
        other = (f["relative_volume"] >= c.min_rel_vol) & (f["atr_pct"] <= c.atr_pct_max)
        # First close outside per episode: the previous bar must NOT already have been outside.
        long_ = (up_out & ~up_out.shift(1, fill_value=False)) & up_ok & other
        short = (dn_out & ~dn_out.shift(1, fill_value=False)) & dn_ok & other
        beyond = pd.concat([f["close"] - hi, lo - f["close"]], axis=1).max(axis=1) / f["atr_14"]
        return Decision(long_, short, _stop_atr(f, long_, short, c.stop_atr), strength=clip01(beyond))


class VWAPMomentumBreakout(LabStrategy):
    name = "S3 VWAP + Momentum Breakout"
    family = "momentum"
    config_cls = VWAPMomentumConfig
    required = ("atr_14", "atr_pct", "vwap", "relative_volume", "minutes_into_day")
    entry_text = ("Close above (below) the 00:00-UTC session VWAP by {c.disp_min_atr}..{c.max_dist_atr} ATR, "
                  "{c.mom_bars}-bar momentum in the same direction, relative volume >= {c.min_rel_vol}, ATR percentile "
                  "<= {c.atr_pct_max}, and at least {c.warmup_min:.0f} min after the VWAP reset. First bar of the "
                  "condition only. Farther than {c.max_dist_atr} ATR from VWAP is treated as exhausted and skipped.")
    invalidation_text = "Close back across VWAP."
    stop_text = "close -/+ {c.stop_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        z = (f["close"] - f["vwap"]) / f["atr_14"]
        mom = f["close"] - f["close"].shift(int(c.mom_bars))
        common = (f["relative_volume"] >= c.min_rel_vol) & (f["atr_pct"] <= c.atr_pct_max) & \
            (f["minutes_into_day"] >= c.warmup_min)
        up = (z >= c.disp_min_atr) & (z <= c.max_dist_atr) & (mom > 0) & common
        dn = (z <= -c.disp_min_atr) & (z >= -c.max_dist_atr) & (mom < 0) & common
        long_, short = rising_edge(up), rising_edge(dn)
        return Decision(long_, short, _stop_atr(f, long_, short, c.stop_atr),
                        strength=clip01((z.abs() - c.disp_min_atr) / (c.max_dist_atr - c.disp_min_atr)))


class SweepMSS(LabStrategy):
    name = "S4 Liquidity Sweep + Market Structure Shift"
    family = "structure"
    config_cls = SweepMSSConfig
    required = ("atr_14",)
    entry_text = ("A wick through the last confirmed ({c.swing_n}-bar fractal) swing low (high) by >= {c.sweep_thr_atr} "
                  "ATR that closes back inside, followed within {c.confirm_window} bars by a close beyond the swing "
                  "high (low) that stood at the time of the sweep (break of structure). Call after a bullish "
                  "sequence, put after a bearish one.")
    invalidation_text = "Price trades beyond the sweep extreme."
    stop_text = "sweep extreme -/+ {c.stop_buf_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        n = len(f)
        h, l, cl, atr = (f[k].to_numpy(float) for k in ("high", "low", "close", "atr_14"))
        sw = P.confirmed_swings(f["high"], f["low"], int(c.swing_n))
        sh, sl = sw["sh"].to_numpy(float), sw["sl"].to_numpy(float)
        long_ = np.zeros(n, bool); short = np.zeros(n, bool)
        stop = np.full(n, np.nan); strength = np.zeros(n)
        pend_bull = None  # (level to break upward, last bar allowed, sweep low)
        pend_bear = None
        w = int(c.confirm_window)
        for i in range(1, n):
            if not np.isfinite(atr[i]):
                continue
            # 1) a sweep from an EARLIER bar can be confirmed by this bar's close (never by the sweep bar itself)
            if pend_bull is not None:
                level, last, ext = pend_bull
                if i > last:
                    pend_bull = None
                elif cl[i] > level:
                    long_[i], stop[i] = True, ext - c.stop_buf_atr * atr[i]
                    strength[i] = min(1.0, (cl[i] - level) / atr[i])
                    pend_bull = None
            if pend_bear is not None:
                level, last, ext = pend_bear
                if i > last:
                    pend_bear = None
                elif cl[i] < level:
                    short[i], stop[i] = True, ext + c.stop_buf_atr * atr[i]
                    strength[i] = min(1.0, (level - cl[i]) / atr[i])
                    pend_bear = None
            # 2) a new sweep of the swing known at the end of the previous bar (wick beyond by the threshold, close back)
            prev_sl, prev_sh = sl[i - 1], sh[i - 1]
            if np.isfinite(prev_sl) and np.isfinite(sh[i]) and l[i] < prev_sl - c.sweep_thr_atr * atr[i]                     and cl[i] > prev_sl:
                pend_bull = (sh[i], i + w, l[i])
            if np.isfinite(prev_sh) and np.isfinite(sl[i]) and h[i] > prev_sh + c.sweep_thr_atr * atr[i]                     and cl[i] < prev_sh:
                pend_bear = (sl[i], i + w, h[i])
        idx = f.index
        return Decision(pd.Series(long_, idx), pd.Series(short, idx), pd.Series(stop, idx),
                        strength=pd.Series(strength, idx))


class FundingOIDivergence(LabStrategy):
    name = "S5 Funding/OI + Price Divergence"
    family = "derivatives"
    config_cls = FundingOIConfig
    required = ("atr_14", "funding_z_7d", "oi_change_z_7d")
    entry_text = ("Price moved >= {c.move_atr} ATR over {c.lookback} bars. mode=0 (fade): OI z-score <= -{c.z_oi} "
                  "(positioning weakening) with funding z beyond +/-{c.z_f} on the same side as the move, then a bar "
                  "closing against the move -> buy the opposite option. mode=1 (follow): OI z-score >= {c.z_oi} "
                  "(positioning building) with funding NOT crowded (|z| < {c.z_f} on that side), then a bar closing "
                  "with the move -> buy the same-direction option. If funding or OI history is missing the answer is "
                  "NO SIGNAL.")
    invalidation_text = "A close back beyond the confirmation bar's extreme."
    stop_text = "close -/+ {c.stop_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        move = (f["close"] - f["close"].shift(int(c.lookback))) / f["atr_14"]
        up, dn = move >= c.move_atr, move <= -c.move_atr
        fz, oz = f["funding_z_7d"], f["oi_change_z_7d"]
        bull_bar, bear_bar = f["close"] > f["open"], f["close"] < f["open"]
        if c.mode == 0:
            weak = oz <= -c.z_oi
            short = up & weak & (fz >= c.z_f) & bear_bar  # rally on weakening, crowded-long positioning -> put
            long_ = dn & weak & (fz <= -c.z_f) & bull_bar  # selloff on weakening, crowded-short positioning -> call
        else:
            building = oz >= c.z_oi
            long_ = up & building & (fz < c.z_f) & bull_bar
            short = dn & building & (fz > -c.z_f) & bear_bar
        strength = clip01((oz.abs() / (2 * c.z_oi) + fz.abs() / (2 * c.z_f)) / 2)
        return Decision(long_, short, _stop_atr(f, long_, short, c.stop_atr), strength=strength)


class LongStraddleExpansion(LabStrategy):
    name = "S6 Long Straddle Volatility Expansion"
    family = "volatility"
    policy = "LONG_STRADDLE"
    config_cls = StraddleConfig
    required = ("atr_14", "rv_pct", "atr_pct", "bbw_pct", "realized_vol_1d")
    entry_text = ("At least {c.min_conditions} of 3 compression conditions: realised-vol percentile <= {c.rv_pct_max}, "
                  "ATR percentile <= {c.atr_pct_max}, Bollinger-width percentile <= {c.bbw_pct_max}; and, only when "
                  "ATM IV history exists, IV percentile <= {c.iv_pct_max}. First bar of the condition only. Buys an "
                  "ATM call AND put.")
    invalidation_text = "No directional invalidation. The premium stop (-35% of the combined premium) and time stop apply."
    stop_text = "none on the underlying; project premium stop on the combined legs"
    target_text = "time stop after {c.max_hold_hours} h, or the project's expiry guard"

    def decide(self, f):
        c = self.config
        n_ok = ((f["rv_pct"] <= c.rv_pct_max).astype(int) + (f["atr_pct"] <= c.atr_pct_max).astype(int)
                + (f["bbw_pct"] <= c.bbw_pct_max).astype(int))
        iv_ok = (f["iv_percentile"] <= c.iv_pct_max) | f["iv_percentile"].isna() if "iv_percentile" in f \
            else pd.Series(True, index=f.index)
        cond = rising_edge((n_ok >= c.min_conditions) & iv_ok)
        no = pd.Series(False, index=f.index)
        return Decision(cond, no, pd.Series(np.nan, index=f.index), strength=clip01(n_ok / 3.0))

    def expected_abs_move(self, f, i):
        rv = f["index_realized_vol_1d"].iloc[i] if "index_realized_vol_1d" in f else np.nan
        rv = rv if np.isfinite(rv) else f["realized_vol_1d"].iloc[i]
        return float(f["close"].iloc[i] * rv * np.sqrt(self.config.hold_hours / 8760) * self.config.move_mult)


class OpeningRangeBreakout(LabStrategy):
    name = "S7 Opening Range Breakout"
    family = "session"
    config_cls = ORBConfig
    required = ("atr_14", "relative_volume")
    entry_text = ("Opening range = high/low of the first {c.range_min} min after the session start (sessions in UTC: "
                  "0=00:00 exchange day, 1=13:30 US cash open, 2=07:00 London; this crypto/gold perp has no official "
                  "session, so these are documented assumptions). Close beyond the range within {c.window_hours} h of "
                  "its completion, range width {c.min_range_atr}..{c.max_range_atr} ATR, relative volume >= "
                  "{c.min_rel_vol}. One signal per range per side (reset_inside={c.reset_inside}).")
    invalidation_text = "Close back through the middle of the range."
    stop_text = "middle of the opening range"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        name, start_min = SESSIONS[c.session]
        ts = pd.to_datetime(f["timestamp"], utc=True)
        shifted = ts - pd.Timedelta(minutes=start_min)
        key = shifted.dt.floor("D")
        since = (shifted - key).dt.total_seconds() / 60.0
        in_range = since < c.range_min
        need_bars = int(round(c.range_min / self.step_min))
        full = in_range.groupby(key).transform("sum") >= need_bars  # a partial range (data gap) is not used
        hi = f["high"].where(in_range).groupby(key).transform("max")
        lo = f["low"].where(in_range).groupby(key).transform("min")
        # Only bars that OPEN after the range completed read it, so every range bar was already closed.
        after = (since >= c.range_min) & (since < c.range_min + c.window_hours * 60) & full
        width = (hi - lo) / f["atr_14"]
        ok = after & (width >= c.min_range_atr) & (width <= c.max_range_atr) & (f["relative_volume"] >= c.min_rel_vol)
        up_out, dn_out = (f["close"] > hi) & ok, (f["close"] < lo) & ok
        if c.reset_inside:
            long_ = up_out & ~up_out.shift(1, fill_value=False)
            short = dn_out & ~dn_out.shift(1, fill_value=False)
        else:
            long_ = up_out & (up_out.astype(int).groupby(key).cumsum() == 1)
            short = dn_out & (dn_out.astype(int).groupby(key).cumsum() == 1)
        mid = (hi + lo) / 2
        beyond = pd.concat([f["close"] - hi, lo - f["close"]], axis=1).max(axis=1) / f["atr_14"]
        return Decision(long_, short, mid, strength=clip01(beyond))


class EMATrendADX(LabStrategy):
    name = "S8 EMA Trend + ADX"
    family = "trend"
    config_cls = EMAADXConfig
    required = ("atr_14", "adx_14", "plus_di_14", "minus_di_14")
    entry_text = ("Bullish: EMA{c.fast} > EMA{c.slow}, close > EMA{c.fast}, +DI > -DI and ADX >= {c.adx_min}; bearish is "
                  "the mirror. Only the first bar the full condition becomes true. Low trend strength (ADX below the "
                  "minimum) is never traded.")
    invalidation_text = "EMA structure or DI ordering flips."
    stop_text = "close -/+ {c.stop_atr} x ATR"
    target_text = "close +/- {c.rr} x the stop distance; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        fast = f["close"].ewm(span=int(c.fast), adjust=False, min_periods=int(c.fast)).mean()
        slow = f["close"].ewm(span=int(c.slow), adjust=False, min_periods=int(c.slow)).mean()
        strong = f["adx_14"] >= c.adx_min
        up = (fast > slow) & (f["close"] > fast) & (f["plus_di_14"] > f["minus_di_14"]) & strong & slow.notna()
        dn = (fast < slow) & (f["close"] < fast) & (f["minus_di_14"] > f["plus_di_14"]) & strong & slow.notna()
        long_, short = rising_edge(up), rising_edge(dn)
        # The slow EMA warm-up is part of the frame requirement: no EMA value, no signal.
        warm = slow.notna()
        long_, short = long_ & warm, short & warm
        return Decision(long_, short, _stop_atr(f, long_, short, c.stop_atr),
                        strength=clip01((f["adx_14"] - c.adx_min) / 25))


class SupertrendVolFilter(LabStrategy):
    name = "S9 Supertrend + Volatility Filter"
    family = "trend"
    config_cls = SupertrendVolConfig
    required = ("atr_14", "atr_pct", "adx_14")
    entry_text = ("Supertrend({c.st_period}, {c.st_mult}) flips direction AND an independent filter passes "
                  "(filter_kind={c.filter_kind}: 0 none/control, 1 ATR percentile in [{c.atr_lo}, {c.atr_hi}], "
                  "2 ADX >= {c.adx_min}).")
    invalidation_text = "Supertrend flips back (the stop is the Supertrend line)."
    stop_text = "the Supertrend line at the flip bar"
    target_text = "close +/- {c.target_atr_mult} x ATR; time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        line, direction = _supertrend(f["high"].to_numpy(float), f["low"].to_numpy(float),
                                      f["close"].to_numpy(float), int(c.st_period), float(c.st_mult))
        line, direction = pd.Series(line, index=f.index), pd.Series(direction, index=f.index)
        flip = direction.notna() & direction.shift(1).notna() & (direction != direction.shift(1))
        if c.filter_kind == 1:
            ok = (f["atr_pct"] >= c.atr_lo) & (f["atr_pct"] <= c.atr_hi)
        elif c.filter_kind == 2:
            ok = f["adx_14"] >= c.adx_min
        else:
            ok = pd.Series(True, index=f.index)
        long_ = flip & (direction == 1) & ok
        short = flip & (direction == -1) & ok
        sign = np.where(long_, 1.0, -1.0)
        target = f["close"] + sign * c.target_atr_mult * f["atr_14"]
        return Decision(long_, short, line, target=target, strength=pd.Series(0.5, index=f.index))


class MeanReversionExtreme(LabStrategy):
    name = "S10 Mean-Reversion Extreme"
    family = "mean_reversion"
    config_cls = MeanRevConfig
    required = ("atr_14", "atr_pct", "vol_expansion", "vwap", "ema_50", "minutes_into_day")
    entry_text = ("|close - fair| / ATR reached >= {c.z_min} (fair: 0=session VWAP, 1=EMA50) within the last "
                  "{c.ext_window} bars, AND the current bar rejects it (wick >= {c.wick_frac} of range, closes with "
                  "the reversion) AND has recovered >= {c.confirm_atr} ATR from the extreme, AND realised vol is not "
                  "expanding faster than {c.vol_exp_max}x and ATR percentile <= {c.atr_pct_max}. Buys the option "
                  "AGAINST the extreme. Overbought/oversold alone never triggers.")
    invalidation_text = "A new extreme beyond the stop."
    stop_text = "the extreme of the last {c.ext_window} bars -/+ {c.stop_buf_atr} x ATR"
    target_text = "the fair-value level (VWAP or EMA50); time stop after {c.max_hold_hours} h"

    def decide(self, f):
        c = self.config
        fair = f["vwap"] if c.fair == 0 else f["ema_50"]
        z = (f["close"] - fair) / f["atr_14"]
        w = int(c.ext_window)
        low_ext, high_ext = f["low"].rolling(w, min_periods=1).min(), f["high"].rolling(w, min_periods=1).max()
        rng = (f["high"] - f["low"]).replace(0, np.nan)
        lower_wick = (np.minimum(f["open"], f["close"]) - f["low"]) / rng
        upper_wick = (f["high"] - np.maximum(f["open"], f["close"])) / rng
        calm = (f["vol_expansion"] <= c.vol_exp_max) & (f["atr_pct"] <= c.atr_pct_max)
        if c.fair == 0:
            calm &= f["minutes_into_day"] >= c.warmup_min
        z_low = ((low_ext - fair) / f["atr_14"]).rolling(w, min_periods=1).min()  # deepest recent deviation (down)
        z_high = ((high_ext - fair) / f["atr_14"]).rolling(w, min_periods=1).max()
        long_ = (z_low <= -c.z_min) & (f["close"] > f["open"]) & (lower_wick >= c.wick_frac) & \
            (f["close"] - low_ext >= c.confirm_atr * f["atr_14"]) & (fair > f["close"]) & calm
        short = (z_high >= c.z_min) & (f["close"] < f["open"]) & (upper_wick >= c.wick_frac) & \
            (high_ext - f["close"] >= c.confirm_atr * f["atr_14"]) & (fair < f["close"]) & calm
        stop = pd.Series(np.where(long_, low_ext - c.stop_buf_atr * f["atr_14"],
                                  high_ext + c.stop_buf_atr * f["atr_14"]), index=f.index)
        return Decision(long_, short, stop, target=fair, strength=clip01(z.abs() / (2 * c.z_min)))


STRATEGY_CLASSES: tuple[type[LabStrategy], ...] = (
    VolBreakoutTrend, DonchianBreakout, VWAPMomentumBreakout, SweepMSS, FundingOIDivergence, LongStraddleExpansion,
    OpeningRangeBreakout, EMATrendADX, SupertrendVolFilter, MeanReversionExtreme,
)
