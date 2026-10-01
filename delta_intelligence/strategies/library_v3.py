"""
Strategy library v3 (user spec, 2026-10-02): 10 setups, executed by BUYING options. No mean-reversion-in-range
strategies.

Each strategy reads columns prepared by `strategies/context.py` (all causal) and declares:
- `expected_hold_bars`: drives the option expiry (DTE >= 2.5 × hold);
- `max_hold_bars`: the time stop;
- `policy`: long option, or straddle/strangle for the event strategy;
- `key_parameters`: perturbed ±20% by the acceptance harness.

Parameter values are fixed in advance and NOT tuned on the test data. The spec forbids tuning until a strategy has
passed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.strategies.base import Strategy, first_per_day

H = 12  # bars per hour (5m)


def _dir(long_: pd.Series, short: pd.Series) -> pd.Series:
    return pd.Series(np.where(long_.fillna(False), 1, np.where(short.fillna(False), -1, 0)), index=long_.index)


def _rr_target(c: pd.Series, stop: pd.Series, d: pd.Series, rr: float) -> pd.Series:
    return c + (c - stop) * rr * np.where(d != 0, 1, np.nan)


class SqueezeBreakout(Strategy):
    name = "Squeeze Breakout"
    description = ("After >= N bars of Bollinger-inside-Keltner squeeze, a displacement close outside the band, taken "
                   "only when ATM IV percentile is low (options cheap).")
    family = "breakout"
    expected_hold_bars, max_hold_bars = 2 * H, 6 * H
    # Spec: squeeze breakout with an IV gate. A displacement requirement is optional (off): with it on, only 3 setups
    # occurred in 10 months of BTC (decided from setup COUNTS before any P&L was looked at).
    default_parameters = {"min_squeeze_bars": 12, "iv_pct_max": 40.0, "stop_atr": 1.5, "rr": 2.0,
                          "require_displacement": 0.0}
    key_parameters = ("min_squeeze_bars", "iv_pct_max", "stop_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        released = (f["squeeze_bars"].shift(1) >= p["min_squeeze_bars"]) & ~f["sq_on"]
        iv_ok = f["iv_percentile"] <= p["iv_pct_max"]
        need = p["require_displacement"] >= 0.5
        up_ok = (f["disp"] == 1) if need else pd.Series(True, index=f.index)
        dn_ok = (f["disp"] == -1) if need else pd.Series(True, index=f.index)
        d = _dir(released & iv_ok & up_ok & (f["close"] > f["bb_up"]),
                 released & iv_ok & dn_ok & (f["close"] < f["bb_dn"]))
        stop = f["close"] - np.sign(d) * p["stop_atr"] * f["atr_14"]
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class LiquiditySweepMSS(Strategy):
    name = "Liquidity Sweep + MSS"
    description = "A sweep of a confirmed swing low/high followed within K bars by a market-structure shift the other way."
    family = "structure"
    expected_hold_bars, max_hold_bars = 3 * H, 8 * H
    default_parameters = {"window": 6, "stop_buffer_atr": 0.25, "rr": 2.5}
    key_parameters = ("window", "stop_buffer_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        w = int(round(p["window"]))
        swept_low = f["sweep_low"].astype(int).rolling(w, min_periods=1).max().astype(bool)
        swept_high = f["sweep_high"].astype(int).rolling(w, min_periods=1).max().astype(bool)
        d = _dir(swept_low & f["mss_up"], swept_high & f["mss_dn"])
        lo, hi = f["low"].rolling(w, min_periods=1).min(), f["high"].rolling(w, min_periods=1).max()
        buf = p["stop_buffer_atr"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, lo - buf, hi + buf), index=f.index)
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class BOSOrderBlockRetest(Strategy):
    name = "BOS + Order Block Retest"
    description = "After a structure-breaking displacement, buy the first reaction from the order block in trend direction."
    family = "structure"
    expected_hold_bars, max_hold_bars = 3 * H, 8 * H
    default_parameters = {"max_ob_age": 36, "stop_buffer_atr": 0.25, "rr": 2.0}
    key_parameters = ("max_ob_age", "stop_buffer_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        fresh = (f["ob_age"] >= 1) & (f["ob_age"] <= p["max_ob_age"])
        bull = fresh & (f["ob_dir"] == 1) & (f["trend"] == 1) & (f["low"] <= f["ob_hi"]) & \
            (f["close"] > f["ob_hi"]) & (f["close"] > f["open"])
        bear = fresh & (f["ob_dir"] == -1) & (f["trend"] == -1) & (f["high"] >= f["ob_lo"]) & \
            (f["close"] < f["ob_lo"]) & (f["close"] < f["open"])
        d = _dir(bull, bear)
        buf = p["stop_buffer_atr"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, f["ob_lo"] - buf, f["ob_hi"] + buf), index=f.index)
        out = self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))
        out["direction"] = pd.Series(np.where(d != d.shift(1), d, 0), index=f.index)  # first reaction only
        return out


class FVGRetraceHTF(Strategy):
    name = "FVG Retrace with HTF Bias"
    description = "In the direction of the 1h bias, buy the reaction when price retraces into an unfilled fair value gap."
    family = "structure"
    expected_hold_bars, max_hold_bars = 3 * H, 8 * H
    default_parameters = {"max_fvg_age": 24, "stop_buffer_atr": 0.5, "rr": 2.0}
    key_parameters = ("max_fvg_age", "stop_buffer_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        fresh = (f["afvg_age"] >= 2) & (f["afvg_age"] <= p["max_fvg_age"])
        bull = fresh & (f["afvg_dir"] == 1) & (f["h1_bias"] == 1) & (f["low"] <= f["afvg_hi"]) & \
            (f["close"] > f["afvg_lo"]) & (f["close"] > f["open"])
        bear = fresh & (f["afvg_dir"] == -1) & (f["h1_bias"] == -1) & (f["high"] >= f["afvg_lo"]) & \
            (f["close"] < f["afvg_hi"]) & (f["close"] < f["open"])
        d = _dir(bull, bear)
        buf = p["stop_buffer_atr"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, f["afvg_lo"] - buf, f["afvg_hi"] + buf), index=f.index)
        out = self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))
        out["direction"] = pd.Series(np.where(d != d.shift(1), d, 0), index=f.index)
        return out


class KillzoneBreakout(Strategy):
    name = "Killzone Breakout"
    description = ("Displacement close beyond the 3-hour pre-killzone range during the London (07-10 Europe/London) or "
                   "New York (08-11 America/New_York) killzone; first break per killzone per side.")
    family = "session"
    expected_hold_bars, max_hold_bars = 2 * H, 5 * H
    default_parameters = {"rr": 2.0, "disp_required": 1.0}
    key_parameters = ("rr",)

    def raw_signals(self, f):
        p = self.parameters
        need_disp = p["disp_required"] >= 0.5
        parts = []
        for kz, pre, key in (("kz_london", "prelon_", "kz_london_key"), ("kz_ny", "preny_", "kz_ny_key")):
            ok = f[kz] & f[f"{pre}complete"]
            up = ok & (f["close"] > f[f"{pre}hi"]) & ((f["disp"] == 1) | (not need_disp))
            dn = ok & (f["close"] < f[f"{pre}lo"]) & ((f["disp"] == -1) | (not need_disp))
            mid = (f[f"{pre}hi"] + f[f"{pre}lo"]) / 2
            parts.append((first_per_day(up, f[key]), first_per_day(dn, f[key]), mid))
        long_ = parts[0][0] | parts[1][0]
        short = parts[0][1] | parts[1][1]
        d = _dir(long_, short)
        mid = parts[0][2].where(f["kz_london"], parts[1][2])
        stop = mid
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class AsiaRangeSweep(Strategy):
    name = "Asia Range Sweep"
    description = ("During the London or New York killzone, a wick through the completed Asia range (20:00-00:00 "
                   "New York) that closes back inside: fade toward the other side (stop beyond the sweep).")
    family = "session"
    expected_hold_bars, max_hold_bars = 3 * H, 8 * H
    default_parameters = {"stop_buffer_atr": 0.25, "rr": 2.0}
    key_parameters = ("stop_buffer_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        ok = f["asia_complete"] & (f["kz_london"] | f["kz_ny"])
        swept_hi = ok & (f["high"] > f["asia_hi"]) & (f["close"] < f["asia_hi"])
        swept_lo = ok & (f["low"] < f["asia_lo"]) & (f["close"] > f["asia_lo"])
        key = f["asia_key"]
        d = _dir(first_per_day(swept_lo, key), first_per_day(swept_hi, key))
        buf = p["stop_buffer_atr"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, f["low"] - buf, f["high"] + buf), index=f.index)
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class TrendPullbackContinuation(Strategy):
    name = "Trend Pullback Continuation"
    description = ("4h and 1h bias aligned; price pulls back to the 1h EMA(50) and resumes with a displacement candle. "
                   "Swing hold (~12 h) so a later expiry is used.")
    family = "trend"
    expected_hold_bars, max_hold_bars = 12 * H, 24 * H
    premium_check_every = 6
    default_parameters = {"touch_pct": 0.002, "stop_atr": 3.0, "rr": 3.0, "cooldown_bars": 2 * H}
    key_parameters = ("touch_pct", "stop_atr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        aligned_up = (f["h4_bias"] == 1) & (f["h1_bias"] == 1)
        aligned_dn = (f["h4_bias"] == -1) & (f["h1_bias"] == -1)
        touched_up = (f["low"].rolling(H, min_periods=1).min() <= f["h1_htf_ema"] * (1 + p["touch_pct"]))
        touched_dn = (f["high"].rolling(H, min_periods=1).max() >= f["h1_htf_ema"] * (1 - p["touch_pct"]))
        d = _dir(aligned_up & touched_up & (f["disp"] == 1) & (f["close"] > f["h1_htf_ema"]),
                 aligned_dn & touched_dn & (f["disp"] == -1) & (f["close"] < f["h1_htf_ema"]))
        stop = f["close"] - np.sign(d) * p["stop_atr"] * f["atr_14"]
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class DailyMomentumSwing(Strategy):
    name = "Daily Momentum Swing"
    description = ("At the start of the exchange day, if 20-day momentum is strong and closes stack above MA20 > MA50 "
                   "(or the mirror), buy a 2-4 week option; ~1 week hold.")
    family = "trend"
    expected_hold_bars, max_hold_bars = 7 * 24 * H, 14 * 24 * H
    premium_check_every = H
    default_parameters = {"roc_min": 0.10, "stop_datr": 2.0, "rr": 2.0}
    key_parameters = ("roc_min", "stop_datr", "rr")

    def raw_signals(self, f):
        p = self.parameters
        day_open = f["minutes_into_day"] == 0
        up = day_open & (f["d_roc20"] >= p["roc_min"]) & (f["d_close"] > f["d_ma20"]) & (f["d_ma20"] > f["d_ma50"])
        dn = day_open & (f["d_roc20"] <= -p["roc_min"]) & (f["d_close"] < f["d_ma20"]) & (f["d_ma20"] < f["d_ma50"])
        d = _dir(up, dn)
        stop = f["close"] - np.sign(d) * p["stop_datr"] * f["d_atr14"]
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class LiquidationFlushReversal(Strategy):
    name = "Liquidation Flush Reversal"
    description = ("A fast move of >= K ATR over the last hour with open interest DROPPING (forced liquidations) and "
                   "funding on the crowded side, then a reversal bar: trade the bounce.")
    family = "derivatives"
    expected_hold_bars, max_hold_bars = 3 * H, 8 * H
    default_parameters = {"move_atr": 4.0, "oi_drop_pct": 1.0, "stop_buffer_atr": 0.25, "rr": 2.0,
                          "cooldown_bars": 2 * H}
    key_parameters = ("move_atr", "oi_drop_pct", "rr")

    def raw_signals(self, f):
        p = self.parameters
        move = f["close"] - f["close"].shift(H)
        oi_drop = f["oi_change_pct_1h"] <= -p["oi_drop_pct"]
        flush_dn = (move <= -p["move_atr"] * f["atr_14"]) & oi_drop & (f["funding_rate"] > 0)
        flush_up = (move >= p["move_atr"] * f["atr_14"]) & oi_drop & (f["funding_rate"] < 0)
        rev_up = (f["close"] > f["open"]) & (f["close"] > f["high"].shift(1))
        rev_dn = (f["close"] < f["open"]) & (f["close"] < f["low"].shift(1))
        recent_dn = flush_dn.astype(int).rolling(3, min_periods=1).max().astype(bool)
        recent_up = flush_up.astype(int).rolling(3, min_periods=1).max().astype(bool)
        d = _dir(recent_dn & rev_up, recent_up & rev_dn)
        buf = p["stop_buffer_atr"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, f["low"].rolling(H, min_periods=1).min() - buf,
                                  f["high"].rolling(H, min_periods=1).max() + buf), index=f.index)
        return self.out(d, stop, _rr_target(f["close"], stop, d, p["rr"]))


class PreEventStraddle(Strategy):
    name = "Pre-Event Long Straddle"
    description = ("Buy an ATM straddle 2-6 h before a scheduled event from config/events.csv when ATM IV percentile is "
                   "low; exit ~2 h after the event. Without an events file it never fires.")
    family = "event"
    policy = "LONG_STRADDLE"
    is_event = True
    expected_hold_bars, max_hold_bars = 6 * H, 8 * H
    default_parameters = {"lead_min_h": 2.0, "lead_max_h": 6.0, "iv_pct_max": 40.0, "move_mult": 1.5}
    key_parameters = ("iv_pct_max", "move_mult")

    def raw_signals(self, f):
        p = self.parameters
        lead = f["hours_to_event"]
        ok = (lead >= p["lead_min_h"]) & (lead <= p["lead_max_h"]) & (f["iv_percentile"] <= p["iv_pct_max"])
        key = f["event_name"].astype(str) + (f["timestamp"] + pd.to_timedelta(lead.fillna(0), unit="h")).dt.floor("h").astype(str)
        first = first_per_day(ok.fillna(False), key)
        d = pd.Series(np.where(first, 2, 0), index=f.index)  # 2 = VOL view (see signals override)
        return pd.DataFrame({"direction": d, "stop": np.nan, "target": np.nan})

    def signals(self, f):
        raw = self.raw_signals(f)
        return pd.DataFrame({"direction": raw["direction"].to_numpy(), "stop": raw["stop"].to_numpy(),
                             "target": raw["target"].to_numpy()}, index=f.index)

    def historical_setups(self, f):
        from delta_intelligence.strategies.base import Setup

        s = self.signals(f)
        p = self.parameters
        hold_h = self.expected_hold_bars / H
        out = []
        for i in np.flatnonzero(s["direction"].to_numpy()):
            c = float(f["close"].iloc[i])
            rv = f["index_realized_vol_1d"].iloc[i] if "index_realized_vol_1d" in f else np.nan
            move = c * (rv if np.isfinite(rv) else 0.5) * np.sqrt(hold_h / 8760) * p["move_mult"]
            out.append(Setup(f["timestamp"].iloc[i], "VOL", c, 0.0, 0.0, self.name,
                             {"expected_abs_move": float(move), "event": f["event_name"].iloc[i]}))
        return out

    def setup_now(self, f):
        if not len(f):
            return None
        setups = [s for s in self.historical_setups(f) if s.timestamp == f["timestamp"].iloc[-1]]
        return setups[0] if setups else None


V3_STRATEGIES: tuple[type[Strategy], ...] = (
    SqueezeBreakout, LiquiditySweepMSS, BOSOrderBlockRetest, FVGRetraceHTF, KillzoneBreakout, AsiaRangeSweep,
    TrendPullbackContinuation, DailyMomentumSwing, LiquidationFlushReversal, PreEventStraddle,
)
