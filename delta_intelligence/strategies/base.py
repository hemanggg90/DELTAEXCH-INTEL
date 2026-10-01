"""
Strategy interface: ONE definition of each strategy's rules, used both for research and for live decisions.

In the reference project every strategy implemented its rules twice, once in `generate_historical_setups` and once
in `check_setup`, and the two copies drifted apart. For example, the live Supertrend check read a
`prev_supertrend_direction` feature that never existed, so it would fire on every bar. Here a strategy implements
only `raw_signals(frame)`: a vectorised, causal function returning, per bar, a direction (+1/-1/0) and the
underlying stop and target. Everything else derives from it:
- `signals(frame)`: raw signals plus geometry checks and the cooldown (a causal sequential pass);
- `historical_setups(frame)`: every signal row;
- `setup_now(frame)`: the signal on the LAST row (the latest closed bar), or None.

So the backtest and the live check are the same code path by construction.

`frame` is the OHLCV of the underlying's perp merged with its features (see `research_frame`). Signals are judged at
a bar's CLOSE; a trade can start at that close at the earliest.

Strategies only propose a direction (or a volatility view) and underlying stop/target. The option BOUGHT to express
it is chosen by the selector (`policy`, plus the expiry implied by `expected_hold_bars`), and the risk engine has the
final word.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

POLICIES = ("LONG_OPTION", "LONG_STRADDLE", "LONG_STRANGLE")  # buying only (plan v3)
STRUCTURES = POLICIES  # compatibility alias


@dataclass
class Setup:
    timestamp: pd.Timestamp  # OPEN time of the signal bar; the decision is taken at its close
    direction: str  # "LONG" / "SHORT"
    entry_price: float  # underlying close of the signal bar
    stop_price: float
    target_price: float
    strategy: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry_price - self.stop_price)


def research_frame(ohlcv: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """OHLCV + features in one frame (same rows). Feature columns that clash with OHLCV names are not expected."""
    f = features.drop(columns=["timestamp"])
    return pd.concat([ohlcv.reset_index(drop=True), f.reset_index(drop=True)], axis=1)


def apply_cooldown(direction: np.ndarray, cooldown: int) -> np.ndarray:
    """Keep a signal only if at least `cooldown` bars have passed since the last KEPT signal (causal)."""
    if cooldown <= 1:
        return direction
    out = np.zeros_like(direction)
    last = -10**9
    for i in np.flatnonzero(direction):
        if i - last >= cooldown:
            out[i] = direction[i]
            last = i
    return out


def first_per_day(event: pd.Series, day: pd.Series) -> pd.Series:
    """True only at the first True of each day (causal: depends on earlier rows of the same day only)."""
    return event & (event.astype(int).groupby(day).cumsum() == 1)


class Strategy(abc.ABC):
    name: str = "base"
    description: str = ""
    family: str = ""  # trend / breakout / reversion / derivatives
    required_features: tuple[str, ...] = ()
    uses_volume: bool = False
    policy: str = "LONG_OPTION"  # LONG_OPTION / LONG_STRADDLE / LONG_STRANGLE
    expected_hold_bars: int = 24  # drives expiry choice (DTE >= multiple x hold)
    max_hold_bars: int = 48  # time stop
    premium_check_every: int = 1  # bars between premium-stop checks (swing strategies check less often)
    is_event: bool = False  # exempt from the IV-percentile gate
    default_parameters: dict = {}

    @property
    def default_structure(self) -> str:  # compatibility alias
        return self.policy

    def __init__(self, parameters: dict | None = None):
        self.parameters = {**self.default_parameters, **(parameters or {})}

    @abc.abstractmethod
    def raw_signals(self, f: pd.DataFrame) -> pd.DataFrame:
        """Columns: direction (int: +1 long, -1 short, 0 none), stop, target. Row i may use rows <= i only."""

    # ---- derived (do not override) -----------------------------------------------------------------------------
    def signals(self, f: pd.DataFrame) -> pd.DataFrame:
        raw = self.raw_signals(f)
        d = raw["direction"].fillna(0).astype(int).to_numpy()
        close, stop, target = f["close"].to_numpy(), raw["stop"].to_numpy(), raw["target"].to_numpy()
        with np.errstate(invalid="ignore"):
            valid_long = (stop < close) & (close < target)
            valid_short = (target < close) & (close < stop)
        d = np.where(((d == 1) & valid_long) | ((d == -1) & valid_short), d, 0)
        d = apply_cooldown(d, int(self.parameters.get("cooldown_bars", 1)))
        return pd.DataFrame({"direction": d, "stop": stop, "target": target}, index=f.index)

    def historical_setups(self, f: pd.DataFrame) -> list[Setup]:
        s = self.signals(f)
        rows = np.flatnonzero(s["direction"].to_numpy())
        return [Setup(f["timestamp"].iloc[i], "LONG" if s["direction"].iloc[i] > 0 else "SHORT",
                      float(f["close"].iloc[i]), float(s["stop"].iloc[i]), float(s["target"].iloc[i]), self.name)
                for i in rows]

    def setup_now(self, f: pd.DataFrame) -> Setup | None:
        """Setup on the latest closed bar (cooldown evaluated over the history in `f`)."""
        if len(f) == 0:
            return None
        s = self.signals(f)
        d = int(s["direction"].iloc[-1])
        if d == 0:
            return None
        return Setup(f["timestamp"].iloc[-1], "LONG" if d > 0 else "SHORT", float(f["close"].iloc[-1]),
                     float(s["stop"].iloc[-1]), float(s["target"].iloc[-1]), self.name)

    # ---- helpers for subclasses --------------------------------------------------------------------------------
    def atr_levels(self, f: pd.DataFrame, direction: pd.Series) -> tuple[pd.Series, pd.Series]:
        sm, tm = self.parameters.get("stop_atr_mult", 1.0), self.parameters.get("target_atr_mult", 2.0)
        sign = np.sign(direction)
        return f["close"] - sign * sm * f["atr_14"], f["close"] + sign * tm * f["atr_14"]

    @staticmethod
    def out(direction: pd.Series, stop: pd.Series, target: pd.Series) -> pd.DataFrame:
        return pd.DataFrame({"direction": direction.fillna(0).astype(int), "stop": stop, "target": target})
