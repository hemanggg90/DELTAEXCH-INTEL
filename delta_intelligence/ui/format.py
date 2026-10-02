"""Number and money formatting for the dashboard: USD as the exchange settles, INR with Indian grouping as an estimate,
and P&L shown with colour AND triangle AND sign (never colour alone)."""
from __future__ import annotations

import math


def indian_group(n: float, decimals: int = 2) -> str:
    """1234567.891 -> '12,34,567.89' (lakh/crore grouping)."""
    if n is None or (isinstance(n, float) and not math.isfinite(n)):
        return "-"
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


def usd(x: float | None, decimals: int = 2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"-${abs(x):,.{decimals}f}" if x < 0 else f"${x:,.{decimals}f}"


def inr(x_usd: float | None, rate: float | None, decimals: int = 0) -> str:
    """INR estimate of a USD amount at `rate` (USDINR_RATE). '-' when no rate is configured."""
    if x_usd is None or rate is None or not math.isfinite(x_usd):
        return "-"
    v = x_usd * rate
    return f"-₹{indian_group(abs(v), decimals)}" if v < 0 else f"₹{indian_group(v, decimals)}"


def money(x_usd: float | None, rate: float | None) -> str:
    """'$1,234.50 (≈ ₹1,03,698)' or just USD when no INR rate is set."""
    s = usd(x_usd)
    return s if rate is None or x_usd is None else f"{s} (≈ {inr(x_usd, rate)})"


def pnl(x: float | None, rate: float | None = None) -> str:
    """'▲ +$12.30' / '▼ -$4.10' / '■ $0.00'. Colour is added by the caller; sign and triangle carry the meaning."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    tri = "▲" if x > 0 else ("▼" if x < 0 else "■")
    sign = "+" if x > 0 else ""
    base = f"{tri} {sign}{usd(x)}"
    return base if rate is None else f"{base} (≈ {inr(x, rate)})"


def pct(x: float | None, decimals: int = 1) -> str:
    return "-" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{decimals}f}%"


def r_mult(x: float | None) -> str:
    return "-" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:+.3f} R"
