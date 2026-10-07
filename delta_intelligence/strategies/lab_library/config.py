"""Strategy configuration objects. Frozen, validated, and changeable without touching code.

Bars here are SIGNAL-timeframe bars (5m or 15m); holds are in hours and converted to 5m bars for the backtester.
Defaults are hypotheses, NOT optimised values. They were fixed before any result was looked at (see PROTOCOL.md).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import ClassVar


class ConfigError(ValueError):
    """A configuration value is outside its allowed range."""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


@dataclass(frozen=True)
class BaseConfig:
    stop_atr: float = 1.5
    rr: float = 2.0
    hold_hours: float = 2.0  # drives the option expiry (DTE >= multiple x hold)
    max_hold_hours: float = 6.0  # time stop
    cooldown_bars: int = 1

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _need(self.stop_atr > 0, "stop_atr must be > 0")
        _need(self.rr > 0, "rr must be > 0")
        _need(0 < self.hold_hours <= self.max_hold_hours, "need 0 < hold_hours <= max_hold_hours")
        _need(self.max_hold_hours <= 24 * 14, "max_hold_hours too large")
        _need(int(self.cooldown_bars) >= 1, "cooldown_bars must be >= 1")

    def as_dict(self) -> dict:
        return asdict(self)

    def with_(self, **kw) -> "BaseConfig":
        return replace(self, **kw)

    key_params: ClassVar[tuple] = ("rr",)


def _pct(x: float, name: str) -> None:
    _need(0.0 < x <= 1.0, f"{name} must be in (0, 1]")


@dataclass(frozen=True)
class VolBreakoutConfig(BaseConfig):
    squeeze_pct: float = 0.20  # BB-width percentile at or below this = compressed
    compress_window: int = 12  # compression must have occurred within the previous N bars
    range_n: int = 20  # breakout of the previous N-bar range
    adx_min: float = 20.0
    ext_atr_pct: float = 0.90  # ATR percentile above this = already extended
    cont_atr: float = 0.25  # continuation exception: close beyond the range by >= this x ATR ...
    cont_rel_vol: float = 1.2  # ... with relative volume >= this
    cooldown_bars: int = 6
    key_params: ClassVar[tuple] = ("squeeze_pct", "range_n", "adx_min", "rr")

    def validate(self) -> None:
        super().validate()
        _pct(self.squeeze_pct, "squeeze_pct"); _pct(self.ext_atr_pct, "ext_atr_pct")
        _need(int(self.compress_window) >= 1 and int(self.range_n) >= 5, "windows too small")
        _need(self.adx_min >= 0 and self.cont_atr >= 0 and self.cont_rel_vol >= 0, "negative threshold")


@dataclass(frozen=True)
class DonchianConfig(BaseConfig):
    n: int = 20
    trend_filter: int = 0  # 1 = require close beyond a rising/falling EMA50 in the same direction
    min_rel_vol: float = 0.0  # 0 = off
    atr_pct_max: float = 1.0  # 1 = off
    key_params: ClassVar[tuple] = ("n", "rr", "stop_atr")

    def validate(self) -> None:
        super().validate()
        _need(int(self.n) >= 5, "n must be >= 5")
        _need(self.trend_filter in (0, 1), "trend_filter must be 0 or 1")
        _need(self.min_rel_vol >= 0, "min_rel_vol must be >= 0")
        _pct(self.atr_pct_max, "atr_pct_max")


@dataclass(frozen=True)
class VWAPMomentumConfig(BaseConfig):
    disp_min_atr: float = 1.0  # minimum |close - VWAP| in ATR
    max_dist_atr: float = 3.0  # anti-chase: maximum |close - VWAP| in ATR
    mom_bars: int = 12
    min_rel_vol: float = 1.0
    atr_pct_max: float = 0.90
    warmup_min: float = 120.0  # minutes after the 00:00 UTC VWAP reset before signals are allowed
    hold_hours: float = 2.0
    max_hold_hours: float = 5.0
    cooldown_bars: int = 6
    key_params: ClassVar[tuple] = ("disp_min_atr", "max_dist_atr", "min_rel_vol", "rr")

    def validate(self) -> None:
        super().validate()
        _need(self.disp_min_atr > 0 and self.max_dist_atr > self.disp_min_atr, "need 0 < disp_min_atr < max_dist_atr")
        _need(int(self.mom_bars) >= 1 and self.min_rel_vol >= 0 and self.warmup_min >= 0, "bad value")
        _pct(self.atr_pct_max, "atr_pct_max")


@dataclass(frozen=True)
class SweepMSSConfig(BaseConfig):
    swing_n: int = 5  # bars each side that confirm a swing
    sweep_thr_atr: float = 0.10  # the wick must pass the level by at least this x ATR
    confirm_window: int = 6  # bars after the sweep within which the structure shift must occur
    stop_buf_atr: float = 0.25
    hold_hours: float = 3.0
    max_hold_hours: float = 8.0
    cooldown_bars: int = 6
    key_params: ClassVar[tuple] = ("swing_n", "sweep_thr_atr", "confirm_window", "rr")

    def validate(self) -> None:
        super().validate()
        _need(int(self.swing_n) >= 2, "swing_n must be >= 2")
        _need(self.sweep_thr_atr >= 0 and self.stop_buf_atr >= 0, "negative threshold")
        _need(int(self.confirm_window) >= 1, "confirm_window must be >= 1")


@dataclass(frozen=True)
class FundingOIConfig(BaseConfig):
    mode: int = 0  # 0 = fade (positioning weakening -> opposite side), 1 = follow (positioning building -> same side)
    lookback: int = 12  # bars over which the price move is measured
    move_atr: float = 1.5
    z_oi: float = 1.0  # 7-day z-score of the 24h OI change
    z_f: float = 1.0  # 7-day funding z-score
    hold_hours: float = 3.0
    max_hold_hours: float = 8.0
    cooldown_bars: int = 12
    key_params: ClassVar[tuple] = ("move_atr", "z_oi", "z_f", "rr")

    def validate(self) -> None:
        super().validate()
        _need(self.mode in (0, 1), "mode must be 0 (fade) or 1 (follow)")
        _need(int(self.lookback) >= 1 and self.move_atr > 0 and self.z_oi > 0 and self.z_f > 0, "bad value")


@dataclass(frozen=True)
class StraddleConfig(BaseConfig):
    rv_pct_max: float = 0.15  # realised-vol percentile at or below this = compressed
    atr_pct_max: float = 0.15
    bbw_pct_max: float = 0.15
    min_conditions: int = 2  # how many of the three compression conditions must hold
    iv_pct_max: float = 100.0  # ATM IV percentile cap, applied only when IV history exists (100 = off)
    move_mult: float = 2.0  # hypothesis: realised vol expands to this multiple of today's, for the breakeven gate
    hold_hours: float = 6.0
    max_hold_hours: float = 12.0
    cooldown_bars: int = 72
    key_params: ClassVar[tuple] = ("rv_pct_max", "atr_pct_max", "bbw_pct_max", "move_mult")

    def validate(self) -> None:
        super().validate()
        for n in ("rv_pct_max", "atr_pct_max", "bbw_pct_max"):
            _pct(getattr(self, n), n)
        _need(self.min_conditions in (1, 2, 3), "min_conditions must be 1, 2 or 3")
        _need(0 < self.iv_pct_max <= 100 and self.move_mult > 0, "bad value")


SESSIONS = {0: ("utc_day", 0), 1: ("us_open", 13 * 60 + 30), 2: ("london", 7 * 60)}  # name, start minute (UTC)


@dataclass(frozen=True)
class ORBConfig(BaseConfig):
    session: int = 1  # 0 = 00:00 UTC exchange day, 1 = 13:30 UTC (US cash open), 2 = 07:00 UTC (London)
    range_min: int = 30
    window_hours: float = 4.0  # signals allowed this long after the range completes
    min_range_atr: float = 0.5
    max_range_atr: float = 6.0
    min_rel_vol: float = 0.0
    reset_inside: int = 0  # 1 = allow a new same-side signal after price closes back inside the range
    hold_hours: float = 2.0
    max_hold_hours: float = 5.0
    key_params: ClassVar[tuple] = ("range_min", "window_hours", "min_range_atr", "rr")

    def validate(self) -> None:
        super().validate()
        _need(self.session in SESSIONS, "session must be 0, 1 or 2")
        _need(int(self.range_min) >= 5 and self.window_hours > 0, "bad range or window")
        _need(0 < self.min_range_atr < self.max_range_atr, "need 0 < min_range_atr < max_range_atr")
        _need(self.reset_inside in (0, 1) and self.min_rel_vol >= 0, "bad value")


@dataclass(frozen=True)
class EMAADXConfig(BaseConfig):
    fast: int = 20
    slow: int = 50
    adx_min: float = 20.0
    stop_atr: float = 2.0
    hold_hours: float = 3.0
    max_hold_hours: float = 8.0
    cooldown_bars: int = 12
    key_params: ClassVar[tuple] = ("fast", "slow", "adx_min", "rr")

    def validate(self) -> None:
        super().validate()
        _need(2 <= int(self.fast) < int(self.slow), "need 2 <= fast < slow")
        _need(self.adx_min >= 0, "adx_min must be >= 0")


@dataclass(frozen=True)
class SupertrendVolConfig(BaseConfig):
    st_period: int = 10
    st_mult: float = 3.0
    filter_kind: int = 1  # 0 = none (control), 1 = ATR-percentile band, 2 = ADX minimum
    atr_lo: float = 0.20
    atr_hi: float = 0.80
    adx_min: float = 20.0
    target_atr_mult: float = 2.25  # same exit geometry as the two active (weak) Supertrend variants
    hold_hours: float = 2.0
    max_hold_hours: float = 4.0

    def validate(self) -> None:
        super().validate()
        _need(int(self.st_period) >= 2 and self.st_mult > 0, "bad supertrend parameters")
        _need(self.filter_kind in (0, 1, 2), "filter_kind must be 0, 1 or 2")
        _need(0 <= self.atr_lo < self.atr_hi <= 1, "need 0 <= atr_lo < atr_hi <= 1")
        _need(self.adx_min >= 0 and self.target_atr_mult > 0, "bad value")

    @property
    def key_params(self) -> tuple:  # type: ignore[override]
        return {0: ("st_mult", "target_atr_mult"), 1: ("st_mult", "atr_lo", "atr_hi"),
                2: ("st_mult", "adx_min")}[self.filter_kind]


@dataclass(frozen=True)
class MeanRevConfig(BaseConfig):
    fair: int = 0  # 0 = session VWAP, 1 = EMA50
    z_min: float = 2.5  # minimum |close - fair| / ATR
    ext_window: int = 3  # the extreme must have been reached within the last N bars
    confirm_atr: float = 0.5  # close must have recovered this x ATR from the extreme
    wick_frac: float = 0.4  # rejection wick as a fraction of the bar range
    vol_exp_max: float = 1.5  # skip when realised vol is expanding faster than this
    atr_pct_max: float = 0.90
    warmup_min: float = 120.0  # only used with VWAP
    stop_buf_atr: float = 0.25
    hold_hours: float = 2.0
    max_hold_hours: float = 5.0
    cooldown_bars: int = 12
    key_params: ClassVar[tuple] = ("z_min", "confirm_atr", "wick_frac", "vol_exp_max")

    def validate(self) -> None:
        super().validate()
        _need(self.fair in (0, 1), "fair must be 0 (VWAP) or 1 (EMA50)")
        _need(self.z_min > 0 and int(self.ext_window) >= 1 and self.confirm_atr >= 0, "bad value")
        _need(0 <= self.wick_frac < 1 and self.vol_exp_max > 0 and self.stop_buf_atr >= 0, "bad value")
        _pct(self.atr_pct_max, "atr_pct_max")
