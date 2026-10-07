"""Hand-built frames and trades for unit tests (offline, deterministic)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_backtest import OptionTrade


def mini_frame(n: int = 300, start: str = "2026-03-02T00:00Z", price: float = 100.0, **over) -> pd.DataFrame:
    """A flat frame with every column the lab strategies read, at neutral values. Override columns by keyword
    (scalars or arrays)."""
    ts = pd.date_range(start, periods=n, freq="5min")
    cols = {
        "timestamp": ts, "open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1.0,
        "atr_14": 1.0, "atr_pct": 0.5, "bbw_pct": 0.5, "rv_pct": 0.5, "adx_14": 30.0, "plus_di_14": 30.0,
        "minus_di_14": 10.0, "ema_20": price, "ema_50": price, "relative_volume": 1.5, "vwap": price,
        "minutes_into_day": ((ts.hour * 60 + ts.minute)).astype(float), "vol_expansion": 1.0,
        "funding_z_7d": 0.0, "oi_change_z_7d": 0.0, "realized_vol_1d": 0.5, "index_realized_vol_1d": 0.5,
        "iv_percentile": np.nan,
    }
    cols.update(over)
    df = pd.DataFrame({k: (v if hasattr(v, "__len__") and not isinstance(v, str) else [v] * n) for k, v in cols.items()})
    df["timestamp"] = ts
    return df


def with_close(df: pd.DataFrame, close: np.ndarray, wick: float = 0.2) -> pd.DataFrame:
    df = df.copy()
    prev = np.concatenate([[close[0]], close[:-1]])
    df["close"], df["open"] = close, prev
    df["high"] = np.maximum(close, prev) + wick
    df["low"] = np.minimum(close, prev) - wick
    return df


def make_trade(net_r: float, when: str, asset_idx: int = 0, fees: float = 0.1, max_loss: float = 10.0,
               regime: str = "RANGE", entry_idx: int = 0, hold: int = 12) -> OptionTrade:
    t0 = pd.Timestamp(when, tz="UTC") if "Z" not in when and "+" not in when else pd.Timestamp(when)
    t = OptionTrade(strategy="x", structure="LONG_CALL", direction="LONG", entry_idx=entry_idx,
                    exit_idx=entry_idx + hold, entry_time=t0, exit_time=t0 + pd.Timedelta(minutes=5 * hold),
                    exit_reason="TARGET", expiry=t0 + pd.Timedelta(days=1), strikes=(100.0,), entry_fills=[1.0],
                    exit_fills=[1.0], entry_iv=0.5, fees=fees, max_loss=max_loss, net_pnl=net_r * max_loss, net_r=net_r,
                    underlying_r=net_r)
    t.regime = regime
    return t
