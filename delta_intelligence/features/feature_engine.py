"""
Market-state feature engine for a 24x7 market.

It turns closed OHLCV bars of the underlying's perpetual (plus optional auxiliary series: spot index, funding, open
interest) into one feature row per bar.

**No look-ahead, by construction:**
- every value at row i uses only bars ≤ i (rolling windows, shifts, cumulative sums, causal EWMs);
- auxiliary series are joined as-of their bar CLOSE (see `alignment.py`);
- day-level levels (CPR, Camarilla) come from the PREVIOUS, complete exchange day;
- the opening range is a running high/low while it forms, flagged by `or_complete`. This fixes a look-ahead in the
  reference project, which assigned the final range to bars still inside it.

`tests/test_features.py` proves this for EVERY column: features computed on a truncated input must equal the full
run up to the cut.

**Days and sessions:**
- The "day" is the exchange day: 00:00 UTC = 05:30 IST by default; see `utils/timeutil.py`.
- Liquidity sessions (Asia, Europe, US, in IST) are multi-label flags because they overlap.

Every column is documented in FEATURE_DEFINITIONS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from delta_intelligence.config.settings import ClockSettings
from delta_intelligence.features.alignment import asof_join, regular_grid
from delta_intelligence.utils.timeutil import FUNDING_INTERVAL, exchange_day_series, parse_sessions, timeframe_seconds

FEATURE_DEFINITIONS: dict[str, str] = {
    # price / trend
    "return_1": "1-bar log return ln(close_t / close_{t-1})",
    "return_5": "5-bar log return",
    "return_20": "20-bar log return",
    "trend_slope": "Slope of a least-squares line through the last 20 closes, divided by the close (per bar)",
    "momentum_20": "Rate of change of close over 20 bars",
    "market_structure": "+1 higher-high & higher-low, -1 lower-high & lower-low, 0 mixed (10-bar window)",
    "ema_20": "20-bar EMA of close", "ema_50": "50-bar EMA of close", "ema_200": "200-bar EMA of close",
    "macd_line": "EMA(12) - EMA(26)", "macd_signal": "9-bar EMA of macd_line", "macd_hist": "macd_line - macd_signal",
    "supertrend": "Supertrend(ATR 10, x3) line", "supertrend_direction": "+1 up (line below price), -1 down",
    "donchian_high_20": "Highest high of the previous 20 bars (excludes current bar)",
    "donchian_low_20": "Lowest low of the previous 20 bars (excludes current bar)",
    "rsi_2": "RSI(2), Wilder smoothing", "rsi_14": "RSI(14), Wilder smoothing",
    "bb_mid_20": "20-bar SMA of close", "bb_upper_20": "bb_mid_20 + 2 std", "bb_lower_20": "bb_mid_20 - 2 std",
    "bb_pct_b": "(close - lower) / (upper - lower)",
    "is_inside_bar": "Current high/low strictly inside the previous bar's range",
    "prev_bar_high": "Previous bar's high", "prev_bar_low": "Previous bar's low",
    # volatility
    "atr_14": "Average True Range over 14 bars (simple mean)",
    "atr_pct_of_price": "atr_14 / close",
    "atr_percentile_100": "Percentile rank of atr_14 within the trailing 100 bars (needs >= 20 values)",
    "realized_vol_20": "Annualised std of 1-bar log returns over 20 bars (365-day, 24h year)",
    "realized_vol_1d": "Annualised std of 1-bar log returns over the last 24 h of bars",
    "vol_expansion": "realized_vol_20 / its 100-bar mean (>1 expanding)",
    # volume
    "relative_volume": "Bar volume / 20-bar mean volume",
    "volume_acceleration": "Change in relative_volume vs the previous bar",
    # exchange-day levels (day starts 00:00 UTC = 05:30 IST)
    "minutes_into_day": "Minutes from the exchange-day start to the bar OPEN",
    "vwap": "Exchange-day cumulative VWAP of typical price (resets at the day start)",
    "vwap_distance_pct": "(close - vwap) / vwap * 100",
    "opening_range_high": "Running high of the first 30 min of the exchange day; held after it completes",
    "opening_range_low": "Running low of the first 30 min of the exchange day; held after it completes",
    "or_complete": "True once the 30-min opening range has fully closed (only then is it a valid breakout level)",
    "cpr_pivot": "Previous complete exchange day's (H+L+C)/3", "cpr_bc": "Previous day's (H+L)/2",
    "cpr_tc": "2*cpr_pivot - cpr_bc",
    "camarilla_r3": "Prev C + (H-L)*1.1/4", "camarilla_r4": "Prev C + (H-L)*1.1/2",
    "camarilla_s3": "Prev C - (H-L)*1.1/4", "camarilla_s4": "Prev C - (H-L)*1.1/2",
    # calendar / clocks
    "session_asia": "Bar open inside the Asia liquidity session (IST window, configurable)",
    "session_europe": "Bar open inside the Europe liquidity session",
    "session_us": "Bar open inside the US liquidity session",
    "is_weekend": "Bar open on Saturday/Sunday UTC (thinner crypto liquidity)",
    "minutes_to_funding": "Minutes from bar CLOSE to the next 00/08/16 UTC funding",
    "hours_to_daily_expiry": "Hours from bar CLOSE to the next 12:00 UTC (17:30 IST) daily option settlement",
    # spot index (options settle on it)
    "index_close": "Spot index close for the same bar (e.g. .DEXBTUSD)",
    "basis_pct": "(perp close - index close) / index close * 100",
    "index_realized_vol_1d": "Annualised 24 h realised vol of the spot index",
    # derivatives (hourly series, as-of joined)
    "funding_rate": "Latest funding rate from the FUNDING series (units UNVERIFIED; see API notes section 7)",
    "funding_z_7d": "Funding rate z-score vs the trailing 7 days of hourly values (std floored at 0.001)",
    "oi": "Open interest (latest closed hourly OI bar)",
    "oi_change_pct_1h": "OI change over 1 hour, %",
    "oi_change_pct_24h": "OI change over 24 hours, %",
    "oi_change_z_7d": "z-score of oi_change_pct_24h vs the trailing 7 days",
    "has_index_data": "Spot index series was supplied", "has_funding_data": "FUNDING series was supplied",
    "has_oi_data": "OI series was supplied",
}

HOURLY = "1h"
# BTC funding sat at exactly 0.01 (the usual 0.01%/8h baseline) for weeks in Aug-Sep 2026, so its 7-day std was 0. A
# small floor keeps the z-score defined (z = 0 at baseline) instead of NaN. The value is in the series' own units.
FUNDING_STD_FLOOR = 0.001


@dataclass
class AuxData:
    """Optional auxiliary inputs. Any may be None; dependent features are then NaN and the `has_*` flag False."""

    index: pd.DataFrame | None = None  # spot index OHLC, same timeframe as the signal bars
    funding: pd.DataFrame | None = None  # FUNDING:<perp> OHLC
    oi: pd.DataFrame | None = None  # OI:<perp> OHLC
    index_timeframe: str | None = None  # defaults to the signal timeframe
    series_timeframe: str = HOURLY
    extra: dict = field(default_factory=dict)


def compute_features(ohlcv: pd.DataFrame, timeframe: str = "5m", aux: AuxData | None = None,
                     clocks: ClockSettings | None = None) -> pd.DataFrame:
    """One feature row per input bar (same order). Input: closed bars with tz-aware UTC OPEN timestamps."""
    clocks = clocks or ClockSettings()
    aux = aux or AuxData()
    df = ohlcv.sort_values("timestamp").reset_index(drop=True)
    raw_ts = pd.to_datetime(df["timestamp"])
    if raw_ts.dt.tz is None:  # never guess a timezone (utc=True would silently assume UTC)
        raise ValueError("timestamps must be tz-aware UTC")
    ts = raw_ts.dt.tz_convert("UTC")
    step = timeframe_seconds(timeframe)
    bars_per_day = max(1, 86400 // step)
    annualise = np.sqrt(365 * bars_per_day)

    out = pd.DataFrame({"timestamp": ts})
    o, h, l, c, v = (df[k].astype("float64") for k in ("open", "high", "low", "close", "volume"))

    # ---- price / trend ---------------------------------------------------------------------------------------
    logc = np.log(c)
    out["return_1"] = logc.diff(1)
    out["return_5"] = logc.diff(5)
    out["return_20"] = logc.diff(20)
    out["trend_slope"] = _rolling_slope(c.to_numpy(), 20) / c
    out["momentum_20"] = c.pct_change(20, fill_method=None)
    out["market_structure"] = _market_structure(h, l, 10)
    for span in (20, 50, 200):
        out[f"ema_{span}"] = c.ewm(span=span, adjust=False, min_periods=span).mean()
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    out["macd_line"] = ema12 - ema26
    out["macd_signal"] = out["macd_line"].ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"] = out["macd_line"] - out["macd_signal"]
    st, st_dir = _supertrend(h.to_numpy(), l.to_numpy(), c.to_numpy(), 10, 3.0)
    out["supertrend"], out["supertrend_direction"] = st, st_dir
    out["donchian_high_20"] = h.shift(1).rolling(20, min_periods=20).max()
    out["donchian_low_20"] = l.shift(1).rolling(20, min_periods=20).min()
    out["rsi_2"] = _rsi(c, 2)
    out["rsi_14"] = _rsi(c, 14)
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std()
    out["bb_mid_20"], out["bb_upper_20"], out["bb_lower_20"] = mid, mid + 2 * sd, mid - 2 * sd
    out["bb_pct_b"] = (c - out["bb_lower_20"]) / (out["bb_upper_20"] - out["bb_lower_20"]).replace(0, np.nan)
    out["prev_bar_high"], out["prev_bar_low"] = h.shift(1), l.shift(1)
    out["is_inside_bar"] = (h < out["prev_bar_high"]) & (l > out["prev_bar_low"])

    # ---- volatility -------------------------------------------------------------------------------------------
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    out["atr_14"] = atr
    out["atr_pct_of_price"] = atr / c
    out["atr_percentile_100"] = _rolling_pct_rank(atr.to_numpy(), 100, 20)
    r1 = out["return_1"]
    out["realized_vol_20"] = r1.rolling(20, min_periods=20).std() * annualise
    out["realized_vol_1d"] = r1.rolling(bars_per_day, min_periods=bars_per_day // 2).std() * annualise
    out["vol_expansion"] = out["realized_vol_20"] / out["realized_vol_20"].rolling(100, min_periods=20).mean()

    # ---- volume -----------------------------------------------------------------------------------------------
    out["relative_volume"] = v / v.rolling(20, min_periods=20).mean().replace(0, np.nan)
    out["volume_acceleration"] = out["relative_volume"].diff(1)

    # ---- exchange day -----------------------------------------------------------------------------------------
    day = exchange_day_series(ts, clocks.exchange_day_offset_min).astype(str)
    day_start = pd.to_datetime(day).dt.tz_localize("UTC") + pd.Timedelta(minutes=clocks.exchange_day_offset_min)
    minutes_into_day = (ts - day_start).dt.total_seconds() / 60.0
    out["minutes_into_day"] = minutes_into_day
    tp = (h + l + c) / 3
    out["vwap"] = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    out["vwap_distance_pct"] = (c - out["vwap"]) / out["vwap"] * 100
    in_or = minutes_into_day < 30
    out["opening_range_high"] = h.where(in_or).groupby(day).cummax().groupby(day).ffill()
    out["opening_range_low"] = l.where(in_or).groupby(day).cummin().groupby(day).ffill()
    day_has_open = minutes_into_day.groupby(day).transform("min") == 0  # partial first day: no valid OR
    out.loc[~day_has_open, ["opening_range_high", "opening_range_low"]] = np.nan
    out["or_complete"] = day_has_open & (minutes_into_day + step / 60.0 >= 30)
    _prior_day_levels(out, h, l, c, day, bars_per_day)

    # ---- clocks -----------------------------------------------------------------------------------------------
    ist_minutes = ((ts.dt.tz_convert("Asia/Kolkata").dt.hour * 60) + ts.dt.tz_convert("Asia/Kolkata").dt.minute)
    for s in parse_sessions(clocks.liquidity_sessions):
        start = s.start.hour * 60 + s.start.minute
        end = s.end.hour * 60 + s.end.minute
        inside = (ist_minutes >= start) & (ist_minutes < end) if start <= end else (ist_minutes >= start) | (ist_minutes < end)
        out[f"session_{s.name.lower()}"] = inside
    out["is_weekend"] = ts.dt.dayofweek >= 5
    close_epoch = (ts - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1) + step
    funding_step = int(FUNDING_INTERVAL.total_seconds())
    out["minutes_to_funding"] = (funding_step - close_epoch % funding_step) / 60.0
    expiry_offset = 12 * 3600  # daily option settlement 12:00 UTC (verified, API notes section 12)
    out["hours_to_daily_expiry"] = (86400 - (close_epoch - expiry_offset) % 86400) / 3600.0

    # ---- spot index -------------------------------------------------------------------------------------------
    if aux.index is not None and len(aux.index):
        idx = asof_join(ts, timeframe, aux.index.rename(columns={"close": "index_close"}),
                        aux.index_timeframe or timeframe, ["index_close"])
        out["index_close"] = idx["index_close"].to_numpy()
        out["basis_pct"] = (c - out["index_close"]) / out["index_close"] * 100
        ir = np.log(out["index_close"]).diff()
        out["index_realized_vol_1d"] = ir.rolling(bars_per_day, min_periods=bars_per_day // 2).std() * annualise
        out["has_index_data"] = True
    else:
        out[["index_close", "basis_pct", "index_realized_vol_1d"]] = np.nan
        out["has_index_data"] = False

    # ---- funding / OI (hourly, as-of) ----------------------------------------------------------------------------
    hourly_per_week = 7 * 24
    if aux.funding is not None and len(aux.funding):
        f = regular_grid(aux.funding.rename(columns={"close": "funding_rate"}), aux.series_timeframe, ["funding_rate"])
        mean = f["funding_rate"].rolling(hourly_per_week, min_periods=48).mean()
        std = f["funding_rate"].rolling(hourly_per_week, min_periods=48).std()
        f["funding_z_7d"] = (f["funding_rate"] - mean) / std.clip(lower=FUNDING_STD_FLOOR)
        j = asof_join(ts, timeframe, f, aux.series_timeframe, ["funding_rate", "funding_z_7d"])
        out["funding_rate"], out["funding_z_7d"] = j["funding_rate"].to_numpy(), j["funding_z_7d"].to_numpy()
        out["has_funding_data"] = True
    else:
        out[["funding_rate", "funding_z_7d"]] = np.nan
        out["has_funding_data"] = False

    if aux.oi is not None and len(aux.oi):
        g = regular_grid(aux.oi.rename(columns={"close": "oi"}), aux.series_timeframe, ["oi"])
        g["oi_change_pct_1h"] = g["oi"].pct_change(1, fill_method=None) * 100
        g["oi_change_pct_24h"] = g["oi"].pct_change(24, fill_method=None) * 100
        m = g["oi_change_pct_24h"].rolling(hourly_per_week, min_periods=48).mean()
        s = g["oi_change_pct_24h"].rolling(hourly_per_week, min_periods=48).std()
        g["oi_change_z_7d"] = (g["oi_change_pct_24h"] - m) / s.replace(0, np.nan)
        cols = ["oi", "oi_change_pct_1h", "oi_change_pct_24h", "oi_change_z_7d"]
        j = asof_join(ts, timeframe, g, aux.series_timeframe, cols)
        for col in cols:
            out[col] = j[col].to_numpy()
        out["has_oi_data"] = True
    else:
        out[["oi", "oi_change_pct_1h", "oi_change_pct_24h", "oi_change_z_7d"]] = np.nan
        out["has_oi_data"] = False

    return out


# ---- helpers ---------------------------------------------------------------------------------------------------
def _prior_day_levels(out: pd.DataFrame, h: pd.Series, l: pd.Series, c: pd.Series, day: pd.Series,
                      bars_per_day: int) -> None:
    """CPR/Camarilla from the PREVIOUS exchange day, only if that day was (nearly) complete in the data."""
    g = pd.DataFrame({"h": h, "l": l, "c": c, "day": day}).groupby("day", sort=True)
    daily = pd.DataFrame({"h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "n": g["c"].size()})
    daily.loc[daily["n"] < 0.9 * bars_per_day, ["h", "l", "c"]] = np.nan  # partial day: no levels from it
    rng = daily["h"] - daily["l"]
    levels = pd.DataFrame({
        "cpr_pivot": (daily["h"] + daily["l"] + daily["c"]) / 3,
        "cpr_bc": (daily["h"] + daily["l"]) / 2,
        "camarilla_r3": daily["c"] + rng * 1.1 / 4, "camarilla_r4": daily["c"] + rng * 1.1 / 2,
        "camarilla_s3": daily["c"] - rng * 1.1 / 4, "camarilla_s4": daily["c"] - rng * 1.1 / 2,
    })
    levels["cpr_tc"] = 2 * levels["cpr_pivot"] - levels["cpr_bc"]
    # Shift by one CALENDAR day (not one row), so a missing day never hands day N-2's levels to day N.
    idx = pd.to_datetime(levels.index)
    levels.index = (idx + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    for col in ("cpr_pivot", "cpr_bc", "cpr_tc", "camarilla_r3", "camarilla_r4", "camarilla_s3", "camarilla_s4"):
        out[col] = day.map(levels[col]).astype("float64").to_numpy()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return rsi.where(loss != 0, 100.0).where(gain.notna())


def _rolling_slope(y: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(y), np.nan)
    if len(y) < window:
        return out
    x = np.arange(window) - (window - 1) / 2
    out[window - 1:] = sliding_window_view(y, window) @ x / (x ** 2).sum()
    return out


def _rolling_pct_rank(a: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """pandas rank(pct=True, method='average') of the last value within each trailing window, NaNs excluded."""
    padded = np.concatenate([np.full(window - 1, np.nan), a])
    w = sliding_window_view(padded, window)
    last = w[:, -1:]
    valid = ~np.isnan(w)
    n = valid.sum(axis=1)
    less = (w < last).sum(axis=1)
    eq = (w == last).sum(axis=1)
    rank = (less + (eq + 1) / 2) / np.where(n == 0, np.nan, n)
    rank[(n < min_periods) | np.isnan(a)] = np.nan
    return rank


def _market_structure(h: pd.Series, l: pd.Series, window: int) -> pd.Series:
    hi = h.rolling(window, min_periods=window).max()
    lo = l.rolling(window, min_periods=window).min()
    up = (h == hi) & (l > lo.shift(1))
    down = (h < hi.shift(1)) & (l == lo)
    return pd.Series(0, index=h.index).mask(up, 1).mask(down, -1).where(hi.notna())


def _supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int, mult: float):
    n = len(c)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    atr = pd.Series(tr).rolling(period, min_periods=period).mean().to_numpy()
    hl2 = (h + l) / 2
    bu, bl = hl2 + mult * atr, hl2 - mult * atr
    fu, fl = bu.copy(), bl.copy()
    direction = np.full(n, np.nan)
    line = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(atr[i - 1]):
            direction[i] = 1.0
            line[i] = fl[i]
            continue
        fu[i] = bu[i] if (bu[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = bl[i] if (bl[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if c[i] > fu[i - 1]:
            direction[i] = 1.0
        elif c[i] < fl[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return line, direction
