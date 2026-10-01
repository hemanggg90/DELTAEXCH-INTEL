"""
As-of alignment of auxiliary series (spot index, funding, OI) onto the signal bars, without look-ahead.

A bar's timestamp is its OPEN time; its values are known only at its CLOSE (open + timeframe). A signal bar closing at
time C may therefore use an auxiliary bar only if that bar had closed by C:

    aux.timestamp + aux_step <= base.timestamp + base_step

Example: the 1h OI bar 10:00-11:00 becomes visible to the 5m bar 10:55-11:00, and not to the 10:50-10:55 bar.
"""
from __future__ import annotations

import pandas as pd

from delta_intelligence.utils.timeutil import timeframe_seconds


def regular_grid(df: pd.DataFrame, timeframe: str, columns: list[str]) -> pd.DataFrame:
    """Reindex onto a gap-free grid, forward-filling gaps from EARLIER bars only (causal).

    The FUNDING series has missing hourly bars, but the rate is constant within each 8 h period, so carrying the last
    known value forward is correct.
    """
    if len(df) == 0:
        return df[["timestamp", *columns]].copy()
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    ts = pd.to_datetime(df["timestamp"], utc=True)
    grid = pd.date_range(ts.iloc[0], ts.iloc[-1], freq=step)
    out = df.assign(timestamp=ts).set_index("timestamp")[columns].reindex(grid).ffill()
    return out.rename_axis("timestamp").reset_index()


def asof_join(base_timestamps: pd.Series, base_timeframe: str, aux: pd.DataFrame, aux_timeframe: str,
              columns: list[str]) -> pd.DataFrame:
    """For each base bar, the latest aux row (of `columns`) that had CLOSED by the base bar's close.

    Returns a frame aligned to `base_timestamps` (same length and order); values are NaN where nothing was available.
    """
    base = pd.DataFrame({"_known_at": pd.to_datetime(base_timestamps, utc=True)
                         + pd.Timedelta(seconds=timeframe_seconds(base_timeframe))})
    base["_order"] = range(len(base))
    if aux is None or len(aux) == 0:
        out = pd.DataFrame(index=range(len(base)), columns=columns, dtype="float64")
        return out
    right = aux[["timestamp", *columns]].copy()
    right["_available_at"] = (pd.to_datetime(right["timestamp"], utc=True)
                              + pd.Timedelta(seconds=timeframe_seconds(aux_timeframe)))
    right = right.drop(columns="timestamp").sort_values("_available_at")
    # Align datetime resolutions (pandas 3 may mix s/us/ns units) before merge_asof.
    base["_known_at"] = base["_known_at"].astype("datetime64[ns, UTC]")
    right["_available_at"] = right["_available_at"].astype("datetime64[ns, UTC]")
    merged = pd.merge_asof(base.sort_values("_known_at"), right, left_on="_known_at", right_on="_available_at",
                           direction="backward", allow_exact_matches=True)
    merged = merged.sort_values("_order").reset_index(drop=True)
    return merged[columns]
