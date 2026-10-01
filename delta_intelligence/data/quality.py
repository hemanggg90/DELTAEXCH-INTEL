"""
Market-data quality gate, adapted for a 24x7 market.

Every dataset entering the pipeline is checked. Only `OK` may trade; `DEGRADED` and `FAIL` force NO TRADE (and the
risk engine vetoes anyway). Poor data must never lead to a silently degraded decision.

| Check | FAIL | DEGRADED |
|---|---|---|
| Timestamps not tz-aware UTC, or off the bar grid | yes | |
| Zero or negative prices | yes | |
| Impossible OHLC (high < low, open/close outside [low, high]) | yes | |
| Fewer than `MIN_ROWS` rows | yes | |
| Duplicate timestamps | | yes |
| Missing bars in the **recent window** (last `recent_window_bars`) | | yes |
| Missing or negative volume | | yes |
| Stale: last closed bar older than expected by more than `staleness_bars` bars | | yes |

Missing bars **older** than the recent window (e.g. an exchange maintenance gap weeks ago) are reported in `issues`
but do not degrade the status. Otherwise one historical outage would force NO TRADE for the whole lookback.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import pandas as pd

from delta_intelligence.utils.timeutil import fmt_ist, last_closed_bar_start, now_utc, timeframe_seconds

QUALITY_OK = "OK"
QUALITY_DEGRADED = "DEGRADED"
QUALITY_FAIL = "FAIL"
MIN_ROWS = 5


@dataclass
class QualityReport:
    status: str
    issues: list[str] = field(default_factory=list)
    n_rows: int = 0
    n_duplicates: int = 0
    n_missing_bars_recent: int = 0
    n_missing_bars_older: int = 0
    n_off_grid: int = 0
    n_ohlc_violations: int = 0
    n_zero_or_negative: int = 0
    n_missing_volume: int = 0
    n_zero_volume: int = 0
    is_stale: bool = False
    last_timestamp: dt.datetime | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["last_timestamp"] = self.last_timestamp.isoformat() if self.last_timestamp else None
        return d


def _ohlc_violation_mask(df: pd.DataFrame) -> pd.Series:
    return ((df["high"] < df["low"]) | (df["close"] > df["high"]) | (df["close"] < df["low"])
            | (df["open"] > df["high"]) | (df["open"] < df["low"]))


def _missing_bar_counts(ts: pd.Series, step: int, recent_from: pd.Timestamp) -> tuple[int, int]:
    """Count missing bars between consecutive timestamps, split by whether the gap ends inside the recent window."""
    secs = ts.diff().dt.total_seconds()
    missing = (secs / step - 1).round().clip(lower=0).fillna(0).astype(int)
    recent = int(missing[ts >= recent_from].sum())
    return recent, int(missing.sum()) - recent


def validate_ohlcv(
    df: pd.DataFrame,
    timeframe: str,
    now: dt.datetime | None = None,
    recent_window_bars: int = 288,
    staleness_bars: int = 2,
    check_volume: bool = True,
    allow_nonpositive: bool = False,
) -> QualityReport:
    """`allow_nonpositive=True` for series that may legitimately be zero or negative (FUNDING rates)."""
    if len(df) == 0:
        return QualityReport(status=QUALITY_FAIL, issues=["no data rows"])
    issues: list[str] = []
    step = timeframe_seconds(timeframe)
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is None:
        return QualityReport(status=QUALITY_FAIL, n_rows=len(df),
                             issues=["timestamps are timezone-naive; expected UTC (refusing to guess a timezone)"])
    ts = ts.dt.tz_convert("UTC")
    order = ts.sort_values().index
    df = df.loc[order]
    ts = ts.loc[order].reset_index(drop=True)

    n_dup = int(ts.duplicated().sum())
    if n_dup:
        issues.append(f"{n_dup} duplicate timestamps")
    ts_u = ts.drop_duplicates()

    epoch = (ts_u - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)  # independent of datetime unit
    n_off_grid = int((epoch % step != 0).sum()) if step < 86400 * 7 else 0
    if n_off_grid:
        issues.append(f"{n_off_grid} bars not aligned to the {timeframe} grid (timezone or resolution error)")

    # recent_window_bars <= 0 means every gap is informational (FUNDING/OI series have known hourly gaps).
    recent_from = (ts_u.iloc[-1] + pd.Timedelta(seconds=1) if recent_window_bars <= 0
                   else ts_u.iloc[-1] - pd.Timedelta(seconds=step * recent_window_bars))
    miss_recent, miss_older = _missing_bar_counts(ts_u.reset_index(drop=True), step, recent_from)
    if miss_recent:
        issues.append(f"{miss_recent} missing bars in the last {recent_window_bars} bars")
    if miss_older:
        issues.append(f"{miss_older} missing bars earlier in the history (informational; e.g. exchange maintenance)")

    prices = df[["open", "high", "low", "close"]]
    bad = prices.isna().any(axis=1) if allow_nonpositive else (prices <= 0).any(axis=1) | prices.isna().any(axis=1)
    n_zero_neg = int(bad.sum())
    if n_zero_neg:
        issues.append(f"{n_zero_neg} rows with " + ("missing values" if allow_nonpositive
                                                     else "zero, negative or missing price"))
    n_ohlc = int(_ohlc_violation_mask(df).sum())
    if n_ohlc:
        issues.append(f"{n_ohlc} rows with impossible OHLC relationships")

    n_missing_vol = n_zero_vol = 0
    if check_volume:
        n_missing_vol = int((df["volume"].isna() | (df["volume"] < 0)).sum())
        if n_missing_vol:
            issues.append(f"{n_missing_vol} rows with missing/negative volume")
        n_zero_vol = int((df["volume"] == 0).sum())
        if n_zero_vol / len(df) > 0.05:
            issues.append(f"{n_zero_vol} zero-volume bars ({n_zero_vol / len(df):.0%}) - thin market (informational)")

    last_ts = ts_u.iloc[-1].to_pydatetime()
    now = now or now_utc()
    expected_last = last_closed_bar_start(now, timeframe)
    behind_bars = (expected_last - last_ts).total_seconds() / step
    is_stale = behind_bars > staleness_bars
    if is_stale:
        issues.append(f"data is stale: last closed bar {fmt_ist(last_ts, '%d %b %H:%M IST')}, "
                      f"{behind_bars:.0f} bars behind {fmt_ist(expected_last, '%d %b %H:%M IST')}")

    hard_fail = n_zero_neg > 0 or n_ohlc > 0 or len(df) < MIN_ROWS or n_off_grid > 0
    degraded = n_dup > 0 or miss_recent > 0 or n_missing_vol > 0 or is_stale
    status = QUALITY_FAIL if hard_fail else QUALITY_DEGRADED if degraded else QUALITY_OK
    return QualityReport(
        status=status, issues=issues, n_rows=len(df), n_duplicates=n_dup, n_missing_bars_recent=miss_recent,
        n_missing_bars_older=miss_older, n_off_grid=n_off_grid, n_ohlc_violations=n_ohlc,
        n_zero_or_negative=n_zero_neg, n_missing_volume=n_missing_vol, n_zero_volume=n_zero_vol,
        is_stale=is_stale, last_timestamp=last_ts,
    )


def clean_ohlcv(df: pd.DataFrame, timeframe: str, now: dt.datetime | None = None) -> pd.DataFrame:
    """Deterministic cleanup: drop duplicate timestamps (keep last), sort, and drop any bar not yet closed at `now`.
    Bad bars are NOT removed here; validate_ohlcv must see and fail them."""
    if len(df) == 0:
        return df
    out = df.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    if now is not None:
        out = out[out["timestamp"] <= pd.Timestamp(last_closed_bar_start(now, timeframe))]
    return out.reset_index(drop=True)
