"""LabStrategy: the project's `Strategy` plus a config object, the standardised signal and NO SIGNAL reasons.

A subclass implements `decide(frame) -> Decision` (boolean long/short masks, stop, optional target, strength). Everything
else is derived: raw signals for the project's backtester, `ResearchSignal` objects, and `evaluate_now` which returns
SIGNAL / NO_SIGNAL / RISK_VETO. Signals are generated on the UNDERLYING only; the project's selector chooses the option.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.strategies.base import Setup, Strategy, apply_cooldown
from lab.config import BaseConfig
from lab.signal import NO_SIGNAL, RISK_VETO, SIGNAL, ResearchSignal, RiskCheck, SignalOutcome

BARS_PER_HOUR_5M = 12


@dataclass
class Decision:
    long: pd.Series  # bool
    short: pd.Series  # bool
    stop: pd.Series  # price; used for long rows where long is True and short rows where short is True
    target: pd.Series | None = None  # price; None = close + rr x (close - stop)
    strength: pd.Series | None = None  # 0..1


def clip01(x: pd.Series) -> pd.Series:
    return x.clip(0.0, 1.0)


class LabStrategy(Strategy):
    config_cls: type[BaseConfig] = BaseConfig
    version = "1.0"
    required: tuple[str, ...] = ("atr_14",)  # columns that must be finite on the signal bar, else NO SIGNAL
    entry_text = invalidation_text = stop_text = target_text = ""

    def __init__(self, config: BaseConfig | None = None, timeframe: str = "5m"):
        self.config = config if config is not None else self.config_cls()
        if not isinstance(self.config, self.config_cls):
            raise TypeError(f"{type(self).__name__} needs a {self.config_cls.__name__}")
        super().__init__(self.config.as_dict())
        self.timeframe = timeframe
        self.step_min = {"5m": 5, "15m": 15}[timeframe]
        # The project's backtester counts 5-minute bars whatever the signal timeframe.
        self.expected_hold_bars = max(1, round(self.config.hold_hours * BARS_PER_HOUR_5M))
        self.max_hold_bars = max(self.expected_hold_bars, round(self.config.max_hold_hours * BARS_PER_HOUR_5M))

    @property
    def is_vol(self) -> bool:
        return self.policy != "LONG_OPTION"

    @property
    def key_parameters(self) -> tuple:  # type: ignore[override]
        return tuple(self.config.key_params)

    # ---- to implement -------------------------------------------------------------------------------------------
    def decide(self, f: pd.DataFrame) -> Decision:
        raise NotImplementedError

    # ---- derived ------------------------------------------------------------------------------------------------
    def missing(self, f: pd.DataFrame) -> pd.Series:
        """True where a required column is absent/NaN (warm-up or no data). Such bars can never signal."""
        miss = pd.Series(False, index=f.index)
        for c in self.required:
            miss |= f[c].isna() if c in f else True
        return miss

    def _masks(self, f: pd.DataFrame) -> tuple[Decision, pd.Series, pd.Series]:
        if any(c not in f for c in self.required):  # an input series is absent altogether: nothing can signal
            no = pd.Series(False, index=f.index)
            return Decision(no, no, pd.Series(np.nan, index=f.index)), no, no
        dec = self.decide(f)
        ok = ~self.missing(f)
        long_ = dec.long.fillna(False).astype(bool) & ok
        short = dec.short.fillna(False).astype(bool) & ok
        both = long_ & short
        return dec, long_ & ~both, short & ~both

    def raw_signals(self, f: pd.DataFrame) -> pd.DataFrame:
        dec, long_, short = self._masks(f)
        d = pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=f.index)
        close = f["close"]
        if dec.target is not None:
            target = dec.target
        else:
            target = close + (close - dec.stop) * self.config.rr
        stop = dec.stop.where(d != 0)
        return self.out(d, stop, target.where(d != 0))

    def signals(self, f: pd.DataFrame) -> pd.DataFrame:
        if not self.is_vol:
            return super().signals(f)
        _, long_, short = self._masks(f)  # a volatility view has no direction, stop or target
        d = np.where(long_ | short, 2, 0)
        d = apply_cooldown(d, int(self.parameters.get("cooldown_bars", 1)))
        nan = np.full(len(f), np.nan)
        return pd.DataFrame({"direction": d, "stop": nan, "target": nan}, index=f.index)

    def expected_abs_move(self, f: pd.DataFrame, i: int) -> float:
        raise NotImplementedError

    def historical_setups(self, f: pd.DataFrame) -> list[Setup]:
        if not self.is_vol:
            return super().historical_setups(f)
        s = self.signals(f)
        return [Setup(f["timestamp"].iloc[i], "VOL", float(f["close"].iloc[i]), 0.0, 0.0, self.name,
                      {"expected_abs_move": self.expected_abs_move(f, i)})
                for i in np.flatnonzero(s["direction"].to_numpy())]

    def setup_now(self, f: pd.DataFrame) -> Setup | None:
        if not len(f):
            return None
        if not self.is_vol:
            return super().setup_now(f)
        last = f["timestamp"].iloc[-1]
        got = [s for s in self.historical_setups(f) if s.timestamp == last]
        return got[0] if got else None

    # ---- standardised signals ---------------------------------------------------------------------------------
    def _signal(self, f: pd.DataFrame, i: int, setup: Setup, dec: Decision, asset: str) -> ResearchSignal:
        strength = float(dec.strength.iloc[i]) if dec.strength is not None and np.isfinite(dec.strength.iloc[i]) else 0.5
        ts = f["timestamp"].iloc[i]
        regime = str(f["regime"].iloc[i]) if "regime" in f else "UNKNOWN"
        direction = {"LONG": "CALL", "SHORT": "PUT", "VOL": "STRADDLE"}[setup.direction]
        conf = {"parameters": self.config.as_dict(), "timeframe": self.timeframe, "policy": self.policy,
                "expected_hold_hours": self.config.hold_hours, "max_hold_hours": self.config.max_hold_hours,
                "data_flags": {c: bool(np.isfinite(f[c].iloc[i])) for c in self.required if c in f}}
        if setup.meta:
            conf.update(setup.meta)
        return ResearchSignal(
            strategy=self.name, version=self.version, asset=asset, timeframe=self.timeframe, timestamp=ts,
            decision_time=ts + pd.Timedelta(minutes=self.step_min), direction=direction,
            strength=max(0.0, min(1.0, strength)), regime=regime, entry_condition=self.entry_text.format(c=self.config),
            invalidation_condition=self.invalidation_text.format(c=self.config),
            stop_logic=self.stop_text.format(c=self.config), target_logic=self.target_text.format(c=self.config),
            stop_price=None if self.is_vol else setup.stop_price,
            target_price=None if self.is_vol else setup.target_price,
            underlying_price=float(f["close"].iloc[i]), confidence=conf)

    def research_signals(self, f: pd.DataFrame, asset: str) -> list[ResearchSignal]:
        dec = self.decide(f)
        pos = {pd.Timestamp(t): i for i, t in enumerate(f["timestamp"])}
        return [self._signal(f, pos[pd.Timestamp(s.timestamp)], s, dec, asset) for s in self.historical_setups(f)]

    def why_no_signal(self, f: pd.DataFrame) -> str:
        miss = self.missing(f).iloc[-1]
        if miss:
            cols = [c for c in self.required if c not in f or pd.isna(f[c].iloc[-1])]
            return f"missing data or warm-up: {', '.join(cols)}"
        return "no valid setup on the latest closed bar"

    def evaluate_now(self, f: pd.DataFrame, asset: str, risk_check: RiskCheck | None = None) -> SignalOutcome:
        """SIGNAL / NO_SIGNAL / RISK_VETO for the latest closed bar. `risk_check` is the project's planner + risk engine
        (see `signal.project_risk_check`); its reason is passed through verbatim."""
        if not len(f):
            return SignalOutcome(NO_SIGNAL, "no data")
        setup = self.setup_now(f)
        if setup is None:
            return SignalOutcome(NO_SIGNAL, self.why_no_signal(f))
        sig = self._signal(f, len(f) - 1, setup, self.decide(f), asset)
        if risk_check is not None:
            approved, reason = risk_check(sig, setup)
            if not approved:
                return SignalOutcome(RISK_VETO, reason, sig)
        return SignalOutcome(SIGNAL, "valid setup", sig)
