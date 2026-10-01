"""
Historical option market model for the hybrid backtest.

A leg's price at time t is:
- **mid** = Black-Scholes(spot index at t, strike, T, σ), where σ = the AS-OF ATM IV (the latest hourly value whose
  hour had closed by t, at most `max_iv_age_hours` old) for the trade's time-to-expiry bucket, plus the fitted smile
  (slope·k + curvature·k², k = ln(K/S)).
- **fill**: buys pay the ask = mid × (1 + h), sells receive the bid = mid × (1 − h), with half-spread
  h(k) = base + slope·|k|. That is never narrower than half a tick, and never assumes better than mid.

Half-spread defaults were calibrated from ONE live snapshot of Delta's books (2026-10-02, 75th percentile, near-ATM,
6-30 h to expiry):
- BTC: ~1.0% at the money, rising to ~2.8% at |k| = 2%;
- ETH: ~1.7% at the money, rising to ~7.7% at |k| = 2%.

Historical spreads are UNVERIFIED (Delta's candles contain trades, not quotes), so these are deliberately pessimistic
and configurable.

If no IV is known for a timestamp (a coverage gap), the model returns None and the backtest SKIPS the trade, counting
the skip. It never invents a price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.options.iv_history import T_BUCKETS_H
from delta_intelligence.options.pricing import bs_price, year_fraction

DEFAULT_SPREAD = {"BTC": (0.010, 0.9), "ETH": (0.017, 3.0)}  # (base, slope per unit |k|)
DEFAULT_TICK = {"BTC": 0.1, "ETH": 0.01}


def _naive_utc(s: pd.Series) -> np.ndarray:
    """tz-aware series -> naive-UTC datetime64[ns] array (tz-aware .to_numpy() would give Python objects)."""
    return pd.to_datetime(s, utc=True).dt.tz_localize(None).to_numpy("datetime64[ns]")


def _ts64(t) -> np.datetime64:
    return np.datetime64(pd.Timestamp(t).tz_convert("UTC").tz_localize(None), "ns")


@dataclass
class OptionMarketModel:
    underlying: str
    index: pd.DataFrame  # 5m spot index OHLC (timestamp = bar open)
    atm_hourly: pd.DataFrame  # from iv_history.atm_iv_hourly
    smile: dict = field(default_factory=dict)
    listed_strikes: dict = field(default_factory=dict)  # expiry Timestamp -> np.ndarray
    symbols: dict = field(default_factory=dict)  # (kind, strike, expiry) -> symbol
    max_iv_age_hours: float = 3.0
    spread: tuple[float, float] | None = None
    tick: float | None = None

    def __post_init__(self):
        self.spread = self.spread or DEFAULT_SPREAD.get(self.underlying, (0.02, 3.0))
        self.tick = self.tick if self.tick is not None else DEFAULT_TICK.get(self.underlying, 0.01)
        self._iv: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name, _, _ in T_BUCKETS_H:
            col = f"atm_iv_{name}"
            if col in self.atm_hourly:
                s = self.atm_hourly.dropna(subset=[col]).sort_values("available_at")
                self._iv[name] = (_naive_utc(s["available_at"]), s[col].to_numpy())
        idx = self.index.sort_values("timestamp")
        self._idx_ts = _naive_utc(idx["timestamp"])
        self._idx_close = idx["close"].to_numpy()

    # ---- inputs ------------------------------------------------------------------------------------------------
    @staticmethod
    def bucket_for(t_hours: float) -> str | None:
        for name, lo, hi in T_BUCKETS_H:
            if lo <= t_hours < hi:
                return name
        return None

    def _asof(self, name: str, at64) -> float | None:
        if name not in self._iv:
            return None
        avail, vals = self._iv[name]
        i = np.searchsorted(avail, at64, side="right") - 1
        if i < 0:
            return None
        age_h = (at64 - avail[i]) / np.timedelta64(1, "h")
        return float(vals[i]) if age_h <= self.max_iv_age_hours else None

    def atm_iv_with_source(self, at: pd.Timestamp, t_hours: float, allow_neighbor: bool = True
                           ) -> tuple[float | None, bool]:
        """(ATM IV as-of `at` for the maturity bucket of `t_hours`, exact_bucket). If that bucket has no fresh value and
        `allow_neighbor`, the nearest maturity bucket with a fresh value is used (shorter first) and exact=False. Such
        trades are labelled MODEL_REAL_IV_ADJ_BUCKET."""
        name = self.bucket_for(t_hours)
        if name is None:
            return None, False
        at64 = _ts64(at)
        v = self._asof(name, at64)
        if v is not None or not allow_neighbor:
            return v, True
        names = [b[0] for b in T_BUCKETS_H]
        k = names.index(name)
        for dist in range(1, len(names)):
            for j in (k - dist, k + dist):
                if 0 <= j < len(names):
                    v = self._asof(names[j], at64)
                    if v is not None:
                        return v, False
        return None, False

    def atm_iv(self, at: pd.Timestamp, t_hours: float) -> float | None:
        return self.atm_iv_with_source(at, t_hours, allow_neighbor=False)[0]

    def leg_iv_with_source(self, at: pd.Timestamp, t_hours: float, strike: float, spot: float,
                           allow_neighbor: bool = True) -> tuple[float | None, bool]:
        atm, exact = self.atm_iv_with_source(at, t_hours, allow_neighbor)
        if atm is None:
            return None, False
        k = math.log(strike / spot)
        sm = self.smile.get(self.bucket_for(t_hours) or "", {})
        iv = atm + sm.get("slope", 0.0) * k + sm.get("curvature", 0.0) * k * k
        return float(min(max(iv, 0.05), 3.0)), exact

    def leg_iv(self, at: pd.Timestamp, t_hours: float, strike: float, spot: float) -> float | None:
        return self.leg_iv_with_source(at, t_hours, strike, spot, allow_neighbor=False)[0]

    def spot_at_close(self, bar_open: pd.Timestamp) -> float | None:
        """Index close of the bar opening at `bar_open` (i.e. the spot at that bar's close)."""
        t64 = _ts64(bar_open)
        i = np.searchsorted(self._idx_ts, t64)
        if i < len(self._idx_ts) and self._idx_ts[i] == t64:
            v = self._idx_close[i]
            return float(v) if np.isfinite(v) else None
        return None

    def settlement_twap(self, expiry: pd.Timestamp) -> float | None:
        """Model of Delta's settlement: mean of the 5m index closes in the 30 min before expiry (verified ~0.012%)."""
        e = _ts64(expiry)
        m = (self._idx_ts >= e - np.timedelta64(30, "m")) & (self._idx_ts < e)
        return float(self._idx_close[m].mean()) if m.sum() >= 3 else None

    # ---- prices ------------------------------------------------------------------------------------------------
    def mid(self, kind: str, strike: float, expiry: pd.Timestamp, at: pd.Timestamp, spot: float) -> tuple[float, float] | None:
        t_sec = (pd.Timestamp(expiry) - pd.Timestamp(at)).total_seconds()
        if t_sec <= 0:
            return (max(spot - strike, 0.0) if kind == "C" else max(strike - spot, 0.0)), 0.0
        iv = self.leg_iv(at, t_sec / 3600.0, strike, spot)
        if iv is None:
            return None
        return float(bs_price(spot, strike, year_fraction(t_sec), iv, kind)), iv

    def half_spread(self, strike: float, spot: float) -> float:
        base, slope = self.spread
        return base + slope * abs(math.log(strike / spot))

    def fill(self, kind: str, strike: float, expiry: pd.Timestamp, at: pd.Timestamp, spot: float,
             buy: bool) -> tuple[float, float, float] | None:
        """(fill price, mid, iv): buys at the modelled ask, sells at the modelled bid."""
        m = self.mid(kind, strike, expiry, at, spot)
        if m is None:
            return None
        mid, iv = m
        h = max(self.half_spread(strike, spot) * mid, self.tick / 2)
        # Round to the tick AGAINST us: buys up, sells down.
        price = math.ceil((mid + h) / self.tick - 1e-9) * self.tick if buy else \
            max(math.floor((mid - h) / self.tick + 1e-9) * self.tick, 0.0)
        return float(price), mid, iv
