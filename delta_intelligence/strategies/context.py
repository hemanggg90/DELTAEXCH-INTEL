"""
Builds the strategy frame: OHLCV + features + structure primitives + IV context + daily context + events. All of it
is causal.

`build_strategy_frame(ohlcv, features, atm_hourly=None, events=None)` returns one row per 5m bar. Strategies read
columns; nothing here looks ahead (see tests/test_primitives.py and tests/test_strategies.py).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from delta_intelligence.strategies import primitives as P
from delta_intelligence.strategies.base import research_frame

SWING_N = 5
IV_BUCKET = "atm_iv_6_30h"
IV_LOOKBACK_HOURS = 60 * 24


def iv_context(ts: pd.Series, atm_hourly: pd.DataFrame | None, bucket: str = IV_BUCKET) -> pd.DataFrame:
    """As-of ATM IV (the latest hourly value AVAILABLE by the bar close) and its percentile versus the trailing 60
    days of earlier values."""
    n = len(ts)
    if atm_hourly is None or bucket not in atm_hourly or not len(atm_hourly):
        return pd.DataFrame({"atm_iv": np.full(n, np.nan), "iv_percentile": np.full(n, np.nan)})
    h = atm_hourly.dropna(subset=[bucket]).sort_values("available_at")
    vals = h[bucket].to_numpy()
    pct = np.full(len(vals), np.nan)
    avail = h["available_at"].to_numpy()
    window = np.timedelta64(IV_LOOKBACK_HOURS, "h")
    start = 0
    for i in range(len(vals)):
        while avail[i] - avail[start] > window:
            start += 1
        prior = vals[start:i]
        if len(prior) >= 100:
            pct[i] = (prior < vals[i]).mean() * 100 + (prior == vals[i]).mean() * 50
    right = pd.DataFrame({"t": pd.to_datetime(h["available_at"], utc=True).astype("datetime64[ns, UTC]"),
                          "atm_iv": vals, "iv_percentile": pct})
    left = pd.DataFrame({"t": (pd.to_datetime(ts, utc=True) + pd.Timedelta(minutes=5)).astype("datetime64[ns, UTC]")})
    left["_o"] = range(n)
    j = pd.merge_asof(left.sort_values("t"), right, on="t", direction="backward",
                      tolerance=pd.Timedelta(hours=3)).sort_values("_o")
    return pd.DataFrame({"atm_iv": j["atm_iv"].to_numpy(), "iv_percentile": j["iv_percentile"].to_numpy()})


def daily_context(ts: pd.Series, o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series) -> pd.DataFrame:
    """Daily (UTC exchange-day) bars from 5m bars. Each 5m bar sees only COMPLETED days: ROC20, MA20, MA50 and daily
    ATR14 of daily closes."""
    t = pd.to_datetime(ts, utc=True)
    s = pd.DataFrame({"o": o.to_numpy(), "h": h.to_numpy(), "l": l.to_numpy(), "c": c.to_numpy()}, index=t)
    d = s.resample("1D").agg({"o": "first", "h": "max", "l": "min", "c": "last"})
    cnt = s["c"].resample("1D").count()
    d = d[cnt >= 280]  # complete days only
    pc = d["c"].shift(1)
    tr = pd.concat([d["h"] - d["l"], (d["h"] - pc).abs(), (d["l"] - pc).abs()], axis=1).max(axis=1)
    feat = pd.DataFrame({"d_roc20": d["c"].pct_change(20, fill_method=None),
                         "d_ma20": d["c"].rolling(20).mean(), "d_ma50": d["c"].rolling(50).mean(),
                         "d_atr14": tr.rolling(14).mean(), "d_close": d["c"]})
    feat["t"] = (feat.index + pd.Timedelta(days=1)).astype("datetime64[ns, UTC]")  # known once the day has closed
    left = pd.DataFrame({"t": (t + pd.Timedelta(minutes=5)).astype("datetime64[ns, UTC]")})
    left["_o"] = range(len(left))
    j = pd.merge_asof(left.sort_values("t"), feat.reset_index(drop=True).sort_values("t"), on="t",
                      direction="backward").sort_values("_o")
    return j.drop(columns=["t", "_o"]).reset_index(drop=True)


def event_context(ts: pd.Series, events: pd.DataFrame | None) -> pd.DataFrame:
    """Hours from bar close to the NEXT scheduled event (calendar entries are known in advance by definition)."""
    n = len(ts)
    if events is None or not len(events):
        return pd.DataFrame({"hours_to_event": np.full(n, np.nan), "event_name": [None] * n})
    close_t = (pd.to_datetime(ts, utc=True) + pd.Timedelta(minutes=5)).to_numpy("datetime64[ns]")
    ev_t = pd.to_datetime(events["timestamp_utc"], utc=True).dt.tz_localize(None).to_numpy("datetime64[ns]")
    idx = np.searchsorted(ev_t, close_t.astype("datetime64[ns]"), side="right")
    has = idx < len(ev_t)
    hours = np.where(has, (ev_t[np.minimum(idx, len(ev_t) - 1)] - close_t) / np.timedelta64(1, "h"), np.nan)
    names = np.where(has, events["name"].to_numpy()[np.minimum(idx, len(ev_t) - 1)], None)
    return pd.DataFrame({"hours_to_event": hours, "event_name": names})


def build_strategy_frame(ohlcv: pd.DataFrame, features: pd.DataFrame, atm_hourly: pd.DataFrame | None = None,
                         events: pd.DataFrame | None = None) -> pd.DataFrame:
    f = research_frame(ohlcv, features)
    ts, o, h, l, c = f["timestamp"], f["open"], f["high"], f["low"], f["close"]
    sw = P.confirmed_swings(h, l, SWING_N)
    disp = P.displacement(o, c, f["atr_14"])
    brk = P.structure_breaks(c, sw)
    swp = P.sweeps(h, l, c, sw)
    ob = P.order_blocks(o, c, h, l, disp, brk)
    afvg = P.active_fvg(P.fvg(h, l), h, l)
    sq = P.squeeze(c, f["atr_14"])
    b1 = P.htf_bias(ts, c, "1h").add_prefix("h1_")
    b4 = P.htf_bias(ts, c, "4h").add_prefix("h4_")
    asia = P.window_range(ts, h, l, P.NEW_YORK, dt.time(20, 0), dt.time(0, 0)).add_prefix("asia_")
    kz_lon = P.in_window(ts, P.LONDON, dt.time(7, 0), dt.time(10, 0)).rename("kz_london")
    kz_ny = P.in_window(ts, P.NEW_YORK, dt.time(8, 0), dt.time(11, 0)).rename("kz_ny")
    pre_lon = P.window_range(ts, h, l, P.LONDON, dt.time(4, 0), dt.time(7, 0)).add_prefix("prelon_")
    pre_ny = P.window_range(ts, h, l, P.NEW_YORK, dt.time(5, 0), dt.time(8, 0)).add_prefix("preny_")
    kz_key_lon = P.session_key(ts, P.LONDON, dt.time(7, 0)).rename("kz_london_key")
    kz_key_ny = P.session_key(ts, P.NEW_YORK, dt.time(8, 0)).rename("kz_ny_key")
    parts = [f, sw, disp.rename("disp"), brk, swp, ob, afvg, sq, b1, b4, asia, kz_lon, kz_ny, pre_lon, pre_ny,
             kz_key_lon, kz_key_ny, iv_context(ts, atm_hourly), daily_context(ts, o, h, l, c), event_context(ts, events)]
    return pd.concat([p.reset_index(drop=True) for p in parts], axis=1)
