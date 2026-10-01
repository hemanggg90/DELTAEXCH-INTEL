"""
The strategy library: 14 well-known setups on the underlying's perp, adapted to a 24x7 market.

Parameters are the reference project's defaults, fixed in advance and NOT tuned on this data. Tuning on the same
history used to measure edge would manufacture edge. Gap Fill was removed (no gaps in 24x7 markets). The four
chain-aware strategies were replaced by two that can be back-tested on real history (funding and open interest).

Default option structures:
- breakout / trend: LONG_OPTION (convex payoff for big moves)
- mean-reversion: DEBIT_SPREAD (a capped target fits a capped structure, and costs less)
- derivatives contrarian: DEBIT_SPREAD
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.strategies.base import Strategy, first_per_day


def _day(f: pd.DataFrame) -> pd.Series:
    """Exchange-day key (the features' minutes_into_day encodes the configured day start)."""
    return (f["timestamp"] - pd.to_timedelta(f["minutes_into_day"], unit="min")).dt.floor("min")


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a.shift(1) <= b_prev) & (a > b)


def _cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a.shift(1) >= b_prev) & (a < b)


def _dir(long: pd.Series, short: pd.Series) -> pd.Series:
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=long.index)


# ---- breakout ------------------------------------------------------------------------------------------------------
class OpeningRangeBreakout(Strategy):
    name = "Opening Range Breakout"
    description = "Close beyond the first 30 min of the exchange day (05:30-06:00 IST); first break per side per day."
    family = "breakout"
    required_features = ("opening_range_high", "opening_range_low", "or_complete", "atr_14")
    default_parameters = {"stop_atr_mult": 0.75, "target_atr_mult": 1.5}

    def raw_signals(self, f):
        ok = f["or_complete"].astype(bool)
        up = ok & (f["close"].shift(1) <= f["opening_range_high"]) & (f["close"] > f["opening_range_high"])
        dn = ok & (f["close"].shift(1) >= f["opening_range_low"]) & (f["close"] < f["opening_range_low"])
        day = _day(f)
        d = _dir(first_per_day(up, day), first_per_day(dn, day))
        return self.out(d, *self.atr_levels(f, d))


class DonchianBreakout(Strategy):
    name = "Donchian Channel Breakout"
    description = "Close above the previous 20-bar high / below the 20-bar low (Turtle-style), with cooldown."
    family = "breakout"
    required_features = ("donchian_high_20", "donchian_low_20", "atr_14")
    default_parameters = {"stop_atr_mult": 2.0, "target_atr_mult": 4.0, "cooldown_bars": 15}

    def raw_signals(self, f):
        d = _dir(f["close"] > f["donchian_high_20"], f["close"] < f["donchian_low_20"])
        return self.out(d, *self.atr_levels(f, d))


class CPRBreakout(Strategy):
    name = "CPR Breakout"
    description = "Close through the previous exchange day's Central Pivot Range (TC/BC)."
    family = "breakout"
    required_features = ("cpr_tc", "cpr_bc", "atr_14", "minutes_into_day")
    default_parameters = {"stop_atr_mult": 1.0, "target_atr_mult": 2.0, "min_minutes_into_day": 15, "cooldown_bars": 10}

    def raw_signals(self, f):
        hi, lo = np.maximum(f["cpr_tc"], f["cpr_bc"]), np.minimum(f["cpr_tc"], f["cpr_bc"])
        ok = f["minutes_into_day"] >= self.parameters["min_minutes_into_day"]
        up = ok & (f["close"].shift(1) <= hi) & (f["close"] > hi)
        dn = ok & (f["close"].shift(1) >= lo) & (f["close"] < lo)
        d = _dir(up, dn)
        return self.out(d, *self.atr_levels(f, d))


class InsideBarBreakout(Strategy):
    name = "Inside Bar Breakout"
    description = "Break of the previous bar's range when that bar was an inside bar; stop at the other side."
    family = "breakout"
    required_features = ("is_inside_bar", "atr_14")
    default_parameters = {"target_atr_mult": 2.0, "cooldown_bars": 3}

    def raw_signals(self, f):
        prev_inside = f["is_inside_bar"].shift(1).fillna(False).astype(bool)
        ph, pl = f["high"].shift(1), f["low"].shift(1)
        d = _dir(prev_inside & (f["close"] > ph), prev_inside & (f["close"] < pl))
        tm = self.parameters["target_atr_mult"]
        stop = pd.Series(np.where(d > 0, pl, ph), index=f.index)
        return self.out(d, stop, f["close"] + np.sign(d) * tm * f["atr_14"])


# ---- trend ---------------------------------------------------------------------------------------------------------
class Momentum(Strategy):
    name = "Momentum"
    description = "Strong 20-bar momentum in the direction of the trend slope, confirmed by above-average volume."
    family = "trend"
    uses_volume = True
    required_features = ("momentum_20", "trend_slope", "relative_volume", "atr_14")
    default_parameters = {"momentum_threshold": 0.003, "min_relative_volume": 1.1, "stop_atr_mult": 1.0,
                          "target_atr_mult": 2.0, "cooldown_bars": 10}

    def raw_signals(self, f):
        p = self.parameters
        vol_ok = f["relative_volume"] >= p["min_relative_volume"]
        d = _dir(vol_ok & (f["momentum_20"] > p["momentum_threshold"]) & (f["trend_slope"] > 0),
                 vol_ok & (f["momentum_20"] < -p["momentum_threshold"]) & (f["trend_slope"] < 0))
        return self.out(d, *self.atr_levels(f, d))


class SupertrendFlip(Strategy):
    name = "Supertrend Trend Following"
    description = "Supertrend(10, 3) direction flip; stop at the Supertrend line."
    family = "trend"
    required_features = ("supertrend", "supertrend_direction", "atr_14")
    default_parameters = {"target_atr_mult": 3.0}

    def raw_signals(self, f):
        sd = f["supertrend_direction"]
        flip = sd.notna() & sd.shift(1).notna() & (sd != sd.shift(1))
        d = _dir(flip & (sd == 1), flip & (sd == -1))
        return self.out(d, f["supertrend"], f["close"] + np.sign(d) * self.parameters["target_atr_mult"] * f["atr_14"])


class EMACrossover(Strategy):
    name = "EMA 50/200 Crossover"
    description = "EMA(50) crossing EMA(200) (golden/death cross); wide ATR exits."
    family = "trend"
    required_features = ("ema_50", "ema_200", "atr_14")
    default_parameters = {"stop_atr_mult": 2.0, "target_atr_mult": 6.0}

    def raw_signals(self, f):
        d = _dir(_cross_up(f["ema_50"], f["ema_200"]), _cross_down(f["ema_50"], f["ema_200"]))
        return self.out(d, *self.atr_levels(f, d))


class MACDCrossover(Strategy):
    name = "MACD Signal Crossover"
    description = "MACD crossing its signal line, in the direction of the EMA(200) trend."
    family = "trend"
    required_features = ("macd_line", "macd_signal", "ema_200", "atr_14")
    default_parameters = {"stop_atr_mult": 1.5, "target_atr_mult": 2.5, "cooldown_bars": 10}

    def raw_signals(self, f):
        up = _cross_up(f["macd_line"], f["macd_signal"]) & (f["close"] > f["ema_200"])
        dn = _cross_down(f["macd_line"], f["macd_signal"]) & (f["close"] < f["ema_200"])
        d = _dir(up, dn)
        return self.out(d, *self.atr_levels(f, d))


# ---- mean reversion ------------------------------------------------------------------------------------------------
class VWAPMeanReversion(Strategy):
    name = "VWAP Mean Reversion"
    description = "Fade a stretch away from the exchange-day VWAP when the trend is weak, on a reversal bar; target VWAP."
    family = "reversion"
    uses_volume = True
    required_features = ("vwap", "vwap_distance_pct", "trend_slope", "atr_14")
    default_parameters = {"stretch_threshold_pct": 0.35, "max_trend_slope": 0.0006, "stop_atr_mult": 1.0,
                          "cooldown_bars": 8}

    def raw_signals(self, f):
        p = self.parameters
        calm = f["trend_slope"].abs() <= p["max_trend_slope"]
        down_bar, up_bar = f["close"] < f["close"].shift(1), f["close"] > f["close"].shift(1)
        d = _dir(calm & (f["vwap_distance_pct"] < -p["stretch_threshold_pct"]) & up_bar,
                 calm & (f["vwap_distance_pct"] > p["stretch_threshold_pct"]) & down_bar)
        stop = f["close"] - np.sign(d) * p["stop_atr_mult"] * f["atr_14"]
        return self.out(d, stop, f["vwap"])


class RSI2MeanReversion(Strategy):
    name = "RSI(2) Mean Reversion"
    description = "Connors RSI(2): buy oversold dips above EMA(200), sell overbought rallies below it."
    family = "reversion"
    required_features = ("rsi_2", "ema_200", "atr_14")
    default_parameters = {"oversold": 10.0, "overbought": 90.0, "stop_atr_mult": 1.5, "target_atr_mult": 1.5,
                          "cooldown_bars": 5}

    def raw_signals(self, f):
        p = self.parameters
        d = _dir((f["close"] > f["ema_200"]) & (f["rsi_2"] <= p["oversold"]),
                 (f["close"] < f["ema_200"]) & (f["rsi_2"] >= p["overbought"]))
        return self.out(d, *self.atr_levels(f, d))


class BollingerReversion(Strategy):
    name = "Bollinger Band Mean Reversion"
    description = "A close back inside the 20-bar Bollinger band after closing outside it, in weak trends; target midline."
    family = "reversion"
    required_features = ("bb_upper_20", "bb_lower_20", "bb_mid_20", "trend_slope", "atr_14")
    default_parameters = {"max_trend_slope": 0.0006, "stop_atr_mult": 1.0, "cooldown_bars": 8}

    def raw_signals(self, f):
        p = self.parameters
        calm = f["trend_slope"].abs() <= p["max_trend_slope"]
        long_ = calm & (f["close"].shift(1) < f["bb_lower_20"].shift(1)) & (f["close"] >= f["bb_lower_20"])
        short = calm & (f["close"].shift(1) > f["bb_upper_20"].shift(1)) & (f["close"] <= f["bb_upper_20"])
        d = _dir(long_, short)
        return self.out(d, f["close"] - np.sign(d) * p["stop_atr_mult"] * f["atr_14"], f["bb_mid_20"])


class CamarillaReversal(Strategy):
    name = "Camarilla Pivot Reversal"
    description = "Fade a touch of the previous day's Camarilla R3/S3 on a reversal bar, back toward the pivot."
    family = "reversion"
    required_features = ("camarilla_r3", "camarilla_s3", "cpr_pivot", "atr_14", "minutes_into_day")
    default_parameters = {"stop_atr_mult": 0.75, "min_minutes_into_day": 15, "cooldown_bars": 10}

    def raw_signals(self, f):
        p = self.parameters
        ok = f["minutes_into_day"] >= p["min_minutes_into_day"]
        ph, pl, pc = f["high"].shift(1), f["low"].shift(1), f["close"].shift(1)
        short = ok & (ph >= f["camarilla_r3"]) & (f["close"] < pc)
        long_ = ok & (pl <= f["camarilla_s3"]) & (f["close"] > pc) & ~short
        d = _dir(long_, short)
        m = p["stop_atr_mult"] * f["atr_14"]
        stop = pd.Series(np.where(d > 0, np.minimum(pl, f["close"] - m), np.maximum(ph, f["close"] + m)), index=f.index)
        return self.out(d, stop, f["cpr_pivot"])


# ---- derivatives (replace the reference's un-backtestable chain strategies) -------------------------------------
class FundingExtremeContrarian(Strategy):
    name = "Funding Extreme Contrarian"
    description = ("Fade crowded positioning: when funding is >= 2 sd above its 7-day norm and RSI(2) turns down from "
                   "overbought, go SHORT (and the mirror for very negative funding). Funding units are UNVERIFIED, but "
                   "a z-score does not depend on them.")
    family = "derivatives"
    required_features = ("funding_z_7d", "rsi_2", "atr_14")
    default_parameters = {"funding_z": 2.0, "stop_atr_mult": 1.5, "target_atr_mult": 2.0, "cooldown_bars": 24}

    def raw_signals(self, f):
        z = self.parameters["funding_z"]
        d = _dir((f["funding_z_7d"] <= -z) & _cross_up(f["rsi_2"], 10.0),
                 (f["funding_z_7d"] >= z) & _cross_down(f["rsi_2"], 90.0))
        return self.out(d, *self.atr_levels(f, d))


class OIBuildupBreakout(Strategy):
    name = "OI Buildup Confirmation"
    description = "Donchian breakout confirmed by rising open interest (new positions, not short covering)."
    family = "derivatives"
    required_features = ("donchian_high_20", "donchian_low_20", "oi_change_pct_1h", "atr_14")
    default_parameters = {"min_oi_change_pct": 0.5, "stop_atr_mult": 1.5, "target_atr_mult": 3.0, "cooldown_bars": 15}

    def raw_signals(self, f):
        rising = f["oi_change_pct_1h"] >= self.parameters["min_oi_change_pct"]
        d = _dir(rising & (f["close"] > f["donchian_high_20"]), rising & (f["close"] < f["donchian_low_20"]))
        return self.out(d, *self.atr_levels(f, d))


ALL_STRATEGIES: tuple[type[Strategy], ...] = (
    OpeningRangeBreakout, Momentum, VWAPMeanReversion, RSI2MeanReversion, BollingerReversion, SupertrendFlip,
    EMACrossover, DonchianBreakout, MACDCrossover, CPRBreakout, CamarillaReversal, InsideBarBreakout,
    FundingExtremeContrarian, OIBuildupBreakout,
)
