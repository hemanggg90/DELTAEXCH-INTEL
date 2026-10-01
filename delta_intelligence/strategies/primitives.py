"""
Price-structure primitives for the v3 strategies. Every function is CAUSAL: the value at bar i depends only on bars
<= i. `tests/test_primitives.py` proves it by truncation and checks that injected leaks are caught.

Definitions (user spec, 2026-10-02):
- **Confirmed swing** (fractal, n bars each side): a swing high at bar p is KNOWN only at bar p+n, once n later bars
  failed to exceed it. Swings are confirmed n bars late; no look-ahead.
- **Displacement:** candle body > 1.5 × ATR(14).
- **Order block:** the last opposite-colour candle before a structure-breaking displacement.
- **FVG:** a 3-candle imbalance. Bullish when low[i] > high[i-2]; bearish when high[i] < low[i-2]. It exists from the
  close of candle i.
- **BOS:** close beyond the last CONFIRMED swing (as of the previous bar). MSS = a BOS against the prevailing
  structure.
- **Liquidity sweep:** a wick through the last confirmed swing that closes back inside.
- **Dealing range:** last confirmed swing low → last confirmed swing high; equilibrium = midpoint.
- **Killzones:** London 07:00-10:00 Europe/London and New York 08:00-11:00 America/New_York local time (DST-safe via
  zoneinfo).
- **Asia range:** 20:00-00:00 America/New_York (the common ICT definition; configurable).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

LONDON = "Europe/London"
NEW_YORK = "America/New_York"


# ---- swings -------------------------------------------------------------------------------------------------------
def confirmed_swings(high: pd.Series, low: pd.Series, n: int = 3) -> pd.DataFrame:
    """Per bar i: the most recent swing high/low CONFIRMED by bar i, with the bar index where it occurred, plus the one
    before it (for structure). Columns: sh, sh_idx, sh_prev, sl, sl_idx, sl_prev, new_sh, new_sl."""
    h, l = high.to_numpy(float), low.to_numpy(float)
    m = len(h)
    sh = np.full(m, np.nan); sh_i = np.full(m, -1.0); sh_p = np.full(m, np.nan)
    sl = np.full(m, np.nan); sl_i = np.full(m, -1.0); sl_p = np.full(m, np.nan)
    new_sh = np.zeros(m, bool); new_sl = np.zeros(m, bool)
    cur_h = cur_hi = prev_h = np.nan
    cur_l = cur_li = prev_l = np.nan
    for i in range(m):
        p = i - n  # candidate pivot whose right side completes at bar i
        if p - n >= 0:
            left_h, right_h = h[p - n:p], h[p + 1:i + 1]
            if h[p] > left_h.max() and h[p] >= right_h.max():
                prev_h, cur_h, cur_hi = cur_h, h[p], p
                new_sh[i] = True
            left_l, right_l = l[p - n:p], l[p + 1:i + 1]
            if l[p] < left_l.min() and l[p] <= right_l.min():
                prev_l, cur_l, cur_li = cur_l, l[p], p
                new_sl[i] = True
        sh[i], sh_i[i], sh_p[i] = cur_h, cur_hi if np.isfinite(cur_hi) else -1, prev_h
        sl[i], sl_i[i], sl_p[i] = cur_l, cur_li if np.isfinite(cur_li) else -1, prev_l
    return pd.DataFrame({"sh": sh, "sh_idx": sh_i, "sh_prev": sh_p, "sl": sl, "sl_idx": sl_i, "sl_prev": sl_p,
                         "new_sh": new_sh, "new_sl": new_sl}, index=high.index)


# ---- candles ------------------------------------------------------------------------------------------------------
def displacement(o: pd.Series, c: pd.Series, atr: pd.Series, mult: float = 1.5) -> pd.Series:
    """+1 bullish / -1 bearish displacement candle (body > mult × ATR), else 0."""
    body = c - o
    big = body.abs() > mult * atr
    return pd.Series(np.where(big, np.sign(body), 0), index=c.index).astype(int)


def fvg(h: pd.Series, l: pd.Series) -> pd.DataFrame:
    """FVG formed at bar i: bull (low[i] > high[i-2]) with zone [high[i-2], low[i]]; bear (high[i] < low[i-2]) with
    zone [high[i], low[i-2]]."""
    h2, l2 = h.shift(2), l.shift(2)
    bull = l > h2
    bear = h < l2
    return pd.DataFrame({"fvg_bull": bull, "fvg_bear": bear,
                         "fvg_lo": np.where(bull, h2, np.where(bear, h, np.nan)),
                         "fvg_hi": np.where(bull, l, np.where(bear, l2, np.nan))}, index=h.index)


# ---- structure ----------------------------------------------------------------------------------------------------
def structure_breaks(c: pd.Series, sw: pd.DataFrame) -> pd.DataFrame:
    """BOS up/down = close crosses the swing confirmed as of the PREVIOUS bar. trend = sign of the last BOS.
    MSS = a BOS against the trend that preceded it."""
    sh_prev_bar, sl_prev_bar = sw["sh"].shift(1), sw["sl"].shift(1)
    bos_up = (c > sh_prev_bar) & (c.shift(1) <= sh_prev_bar)
    bos_dn = (c < sl_prev_bar) & (c.shift(1) >= sl_prev_bar)
    ev = pd.Series(np.where(bos_up, 1, np.where(bos_dn, -1, 0)), index=c.index)
    trend_before = ev.replace(0, np.nan).ffill().shift(1).fillna(0)
    trend = ev.replace(0, np.nan).ffill().fillna(0)
    return pd.DataFrame({"bos_up": bos_up, "bos_dn": bos_dn, "trend": trend.astype(int),
                         "mss_up": bos_up & (trend_before < 0), "mss_dn": bos_dn & (trend_before > 0)}, index=c.index)


def sweeps(h: pd.Series, l: pd.Series, c: pd.Series, sw: pd.DataFrame) -> pd.DataFrame:
    """Sweep of the last confirmed swing (as of the previous bar): wick beyond it, close back inside."""
    sh_prev_bar, sl_prev_bar = sw["sh"].shift(1), sw["sl"].shift(1)
    return pd.DataFrame({"sweep_high": (h > sh_prev_bar) & (c < sh_prev_bar),
                         "sweep_low": (l < sl_prev_bar) & (c > sl_prev_bar)}, index=c.index)


def order_blocks(o: pd.Series, c: pd.Series, h: pd.Series, l: pd.Series, disp: pd.Series, brk: pd.DataFrame,
                 lookback: int = 10) -> pd.DataFrame:
    """At each structure-breaking displacement (BOS in the displacement's direction), record the last opposite candle
    within `lookback` bars as the active order block. The zone stays active (as of each bar) until invalidated by a
    close through its far side, or replaced. Columns: ob_dir (+1 bull / -1 bear / 0), ob_lo, ob_hi, ob_age."""
    O, C, H, L = (x.to_numpy(float) for x in (o, c, h, l))
    D = disp.to_numpy()
    up, dn = brk["bos_up"].to_numpy(), brk["bos_dn"].to_numpy()
    m = len(C)
    out_dir = np.zeros(m, int); out_lo = np.full(m, np.nan); out_hi = np.full(m, np.nan); age = np.full(m, -1)
    cur_dir, cur_lo, cur_hi, born = 0, np.nan, np.nan, -1
    for i in range(m):
        if cur_dir == 1 and C[i] < cur_lo:
            cur_dir = 0
        elif cur_dir == -1 and C[i] > cur_hi:
            cur_dir = 0
        if D[i] == 1 and up[i]:
            for j in range(i - 1, max(-1, i - 1 - lookback), -1):
                if C[j] < O[j]:
                    cur_dir, cur_lo, cur_hi, born = 1, L[j], H[j], i
                    break
        elif D[i] == -1 and dn[i]:
            for j in range(i - 1, max(-1, i - 1 - lookback), -1):
                if C[j] > O[j]:
                    cur_dir, cur_lo, cur_hi, born = -1, L[j], H[j], i
                    break
        out_dir[i] = cur_dir
        if cur_dir:
            out_lo[i], out_hi[i], age[i] = cur_lo, cur_hi, i - born
    return pd.DataFrame({"ob_dir": out_dir, "ob_lo": out_lo, "ob_hi": out_hi, "ob_age": age}, index=c.index)


def active_fvg(f: pd.DataFrame, h: pd.Series, l: pd.Series, max_age: int = 48) -> pd.DataFrame:
    """The most recent unfilled FVG as of each bar (formed at a bar <= i, not yet fully traded through)."""
    H, L = h.to_numpy(float), l.to_numpy(float)
    bull, bear = f["fvg_bull"].to_numpy(), f["fvg_bear"].to_numpy()
    lo_a, hi_a = f["fvg_lo"].to_numpy(float), f["fvg_hi"].to_numpy(float)
    m = len(H)
    d = np.zeros(m, int); lo = np.full(m, np.nan); hi = np.full(m, np.nan); age = np.full(m, -1)
    cd, clo, chi, born = 0, np.nan, np.nan, -1
    for i in range(m):
        if cd == 1 and L[i] < clo:  # traded fully through the bullish gap: filled/invalid
            cd = 0
        elif cd == -1 and H[i] > chi:
            cd = 0
        if bull[i]:
            cd, clo, chi, born = 1, lo_a[i], hi_a[i], i
        elif bear[i]:
            cd, clo, chi, born = -1, lo_a[i], hi_a[i], i
        if cd and i - born > max_age:
            cd = 0
        d[i] = cd
        if cd:
            lo[i], hi[i], age[i] = clo, chi, i - born
    return pd.DataFrame({"afvg_dir": d, "afvg_lo": lo, "afvg_hi": hi, "afvg_age": age}, index=h.index)


# ---- sessions -----------------------------------------------------------------------------------------------------
def local_time(ts: pd.Series, tz: str) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(tz)


def in_window(ts: pd.Series, tz: str, start: dt.time, end: dt.time) -> pd.Series:
    """Bar OPEN inside [start, end) local time in `tz` (DST-aware). Windows may cross midnight."""
    lt = local_time(ts, tz)
    mins = lt.dt.hour * 60 + lt.dt.minute
    s, e = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def session_key(ts: pd.Series, tz: str, start: dt.time) -> pd.Series:
    """Date label of the session that started at `start` local time (windows crossing midnight belong to the start
    day)."""
    lt = local_time(ts, tz)
    shifted = lt - pd.Timedelta(hours=start.hour, minutes=start.minute)
    return shifted.dt.date.astype(str)


def window_range(ts: pd.Series, h: pd.Series, l: pd.Series, tz: str, start: dt.time, end: dt.time) -> pd.DataFrame:
    """Running high/low of the window while it forms; after it closes, its final high/low is carried to later bars
    (until the next window starts). `complete` = the window has finished. Causal: a bar only sees bars <= itself."""
    inside = in_window(ts, tz, start, end)
    key = session_key(ts, tz, start)
    hi = h.where(inside).groupby(key).cummax().groupby(key).ffill()
    lo = l.where(inside).groupby(key).cummin().groupby(key).ffill()
    started = inside.groupby(key).cummax()
    complete = started & ~inside
    return pd.DataFrame({"hi": hi.where(started), "lo": lo.where(started), "inside": inside, "complete": complete,
                         "key": key}, index=h.index)


# ---- higher timeframe -------------------------------------------------------------------------------------------------
def htf_bias(ts: pd.Series, c: pd.Series, rule: str = "1h", ema_span: int = 50) -> pd.DataFrame:
    """Bias from COMPLETED higher-timeframe bars: +1 when the last completed HTF close is above its EMA and the EMA is
    rising, -1 for the mirror, else 0. A 5m bar sees the HTF bar only after that bar has closed."""
    s = pd.Series(c.to_numpy(), index=pd.to_datetime(ts, utc=True))
    htf = s.resample(rule, label="left", closed="left").last().dropna()
    ema = htf.ewm(span=ema_span, adjust=False, min_periods=ema_span).mean()
    bias = pd.Series(np.where((htf > ema) & (ema.diff() > 0), 1, np.where((htf < ema) & (ema.diff() < 0), -1, 0)),
                     index=htf.index).where(ema.notna())
    avail = pd.DataFrame({"t": htf.index + pd.Timedelta(rule), "bias": bias.to_numpy(), "htf_ema": ema.to_numpy(),
                          "htf_close": htf.to_numpy()}).dropna(subset=["htf_ema"])
    known = pd.DataFrame({"t": pd.to_datetime(ts, utc=True) + pd.Timedelta(minutes=5)})
    known["_o"] = range(len(known))
    avail["t"] = avail["t"].astype("datetime64[ns, UTC]")
    known["t"] = known["t"].astype("datetime64[ns, UTC]")
    j = pd.merge_asof(known.sort_values("t"), avail.sort_values("t"), on="t", direction="backward").sort_values("_o")
    return pd.DataFrame({"bias": j["bias"].to_numpy(), "htf_ema": j["htf_ema"].to_numpy(),
                         "htf_close": j["htf_close"].to_numpy()}, index=c.index)


# ---- squeeze ------------------------------------------------------------------------------------------------------
def squeeze(c: pd.Series, atr: pd.Series, n: int = 20, bb_k: float = 2.0, kc_k: float = 1.5) -> pd.DataFrame:
    """TTM-style squeeze: Bollinger(20, 2) inside Keltner(EMA20 ± 1.5 ATR). `squeeze_bars` = consecutive bars in
    squeeze up to and including i."""
    mid = c.rolling(n, min_periods=n).mean()
    sd = c.rolling(n, min_periods=n).std()
    ema = c.ewm(span=n, adjust=False, min_periods=n).mean()
    on = ((mid + bb_k * sd) < (ema + kc_k * atr)) & ((mid - bb_k * sd) > (ema - kc_k * atr))
    run = on.astype(int).groupby((~on).cumsum()).cumsum()
    return pd.DataFrame({"sq_on": on, "squeeze_bars": run, "bb_up": mid + bb_k * sd, "bb_dn": mid - bb_k * sd},
                        index=c.index)
