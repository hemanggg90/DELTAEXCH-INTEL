"""Number and money formatting for the dashboard: USD as the exchange settles, INR with Indian grouping as an estimate,
and P&L shown with colour AND triangle AND sign (never colour alone). Missing values render as "–", never nan/None."""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

MISSING = "–"


def _missing(x) -> bool:
    if x is None:
        return True
    try:
        return not math.isfinite(float(x))
    except (TypeError, ValueError):
        return True


def indian_group(n: float, decimals: int = 2) -> str:
    """1234567.891 -> '12,34,567.89' (lakh/crore grouping)."""
    if _missing(n):
        return MISSING
    neg = n < 0
    s = f"{abs(n):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = whole + (f".{frac}" if decimals else "")
    return f"-{out}" if neg else out


def num(x, dp: int = 2) -> str:
    return MISSING if _missing(x) else f"{float(x):,.{dp}f}"


def usd(x: float | None, decimals: int = 2, signed: bool = False) -> str:
    if _missing(x):
        return MISSING
    x = float(x)
    if x < 0:
        return f"-${abs(x):,.{decimals}f}"
    return f"{'+' if signed and x > 0 else ''}${x:,.{decimals}f}"


def inr(x_usd: float | None, rate: float | None, decimals: int = 0) -> str:
    """INR estimate of a USD amount at `rate` (USDINR_RATE). '–' when no rate is configured."""
    if _missing(x_usd) or rate is None:
        return MISSING
    v = float(x_usd) * rate
    return f"-₹{indian_group(abs(v), decimals)}" if v < 0 else f"₹{indian_group(v, decimals)}"


def money(x_usd: float | None, rate: float | None = None, dp: int = 2, signed: bool = False) -> str:
    """'$1,234.50 (≈ ₹1,03,698)' or just USD when no INR rate is set."""
    s = usd(x_usd, dp, signed)
    return s if rate is None or _missing(x_usd) else f"{s} (≈ {inr(x_usd, rate)})"


def arrow(x) -> str:
    if _missing(x) or float(x) == 0:
        return "–"
    return "▲" if float(x) > 0 else "▼"


def tone(x) -> str:
    """good / critical / neutral by sign."""
    if _missing(x) or float(x) == 0:
        return "neutral"
    return "good" if float(x) > 0 else "critical"


def pnl(x: float | None, rate: float | None = None) -> str:
    """'▲ +$12.30' / '▼ -$4.10' / '■ $0.00'. Colour is added by the caller; sign and triangle carry the meaning."""
    if _missing(x):
        return MISSING
    x = float(x)
    tri = "▲" if x > 0 else ("▼" if x < 0 else "■")
    base = f"{tri} {usd(x, signed=True)}"
    return base if rate is None else f"{base} (≈ {inr(x, rate)})"


def pnl_inr(x_usd: float | None, rate: float | None) -> str:
    """INR estimate of a P&L: '▲ +₹1,034' / '▼ -₹345' / '■ ₹0'."""
    if _missing(x_usd) or rate is None:
        return MISSING
    x = float(x_usd)
    tri = "▲" if x > 0 else ("▼" if x < 0 else "■")
    return f"{tri} {'+' if x > 0 else ''}{inr(x, rate)}"


def pct(x: float | None, decimals: int = 1, signed: bool = False) -> str:
    if _missing(x):
        return MISSING
    return f"{float(x):+.{decimals}f}%" if signed else f"{float(x):.{decimals}f}%"


def r_mult(x: float | None) -> str:
    return MISSING if _missing(x) else f"{float(x):+.3f} R"


def duration(td: dt.timedelta | float | None) -> str:
    """timedelta (or seconds) -> '45s' / '45m' / '2h 14m' / '3d 4h'. Negative durations get a leading '-'."""
    if td is None:
        return MISSING
    secs = td.total_seconds() if isinstance(td, dt.timedelta) else float(td)
    if not math.isfinite(secs):
        return MISSING
    sign, secs = ("-" if secs < 0 else ""), abs(int(secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{sign}{d}d {h}h"
    if h:
        return f"{sign}{h}h {m}m"
    if m == 0 and secs:
        return f"{sign}{secs}s"
    return f"{sign}{m}m"


def time_left(ts, now) -> str:
    """Countdown from `now` to `ts` (both tz-aware)."""
    if ts is None:
        return MISSING
    return duration(pd.Timestamp(ts) - pd.Timestamp(now))


def text(x) -> str:
    """Any value for display; missing -> '–'."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return MISSING
    return str(x)
