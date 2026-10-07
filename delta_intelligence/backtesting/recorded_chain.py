"""
As-of access to RECORDED option quotes (REAL_RECORDED data) for the backtester.

Everything here is strictly causal: a lookup at time `t` sees only snapshots taken at or before `t`, and only if the
snapshot is not older than `max_age_sec`. There is no interpolation and no filling: if no fresh snapshot exists the
answer is None, and the caller decides (REAL_ONLY skips the trade; REAL_THEN_MODEL_FALLBACK models it and labels it).

A quote's `tier` comes from `options.chain_quality.assess`:
- REAL_QUOTE: a usable two-sided quote (entry may use the ask, exit the bid);
- REAL_MARK_ONLY: a mark but no usable bid/ask (never treated as bid or ask);
- UNUSABLE.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.options.chain_quality import REAL_MARK_ONLY, REAL_QUOTE, UNUSABLE

__all__ = ["RealQuote", "RecordedQuotes", "REAL_QUOTE", "REAL_MARK_ONLY", "UNUSABLE"]


def _f(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


@dataclass(frozen=True)
class RealQuote:
    taken_at: pd.Timestamp
    age_sec: float  # query time minus snapshot time
    tier: str
    bid: float | None
    ask: float | None
    mark: float | None
    mark_iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    open_interest: float | None
    volume: float | None
    bid_size: float | None
    ask_size: float | None
    spot: float | None
    symbol: str = ""

    @property
    def mid(self) -> float | None:
        if self.tier == REAL_QUOTE and self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return None

    @property
    def spread_pct(self) -> float | None:
        m = self.mid
        return None if m is None else (self.ask - self.bid) / m * 100.0


_COLS = ("bid", "ask", "mark", "mark_iv", "delta", "gamma", "theta", "vega", "open_interest", "volume", "bid_size",
         "ask_size", "spot", "tier", "symbol")


def _naive_utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


class RecordedQuotes:
    """Recorded quotes for ONE underlying, from `assess()` output (rows already flagged and tiered). Times are kept as
    naive-UTC internally and returned tz-aware (UTC)."""

    def __init__(self, assessed: pd.DataFrame, underlying: str, max_age_sec: float = 600.0):
        self.underlying = underlying
        self.max_age = pd.Timedelta(seconds=max_age_sec)
        d = assessed[(assessed["underlying"] == underlying) & (assessed["tier"] != UNUSABLE)].copy()
        d["_t"] = pd.to_datetime(d["taken_at"], utc=True).dt.tz_localize(None).astype("datetime64[ns]")
        d["_e"] = pd.to_datetime(d["expiry"], utc=True).dt.tz_localize(None).astype("datetime64[ns]")
        d = d.sort_values("_t")
        self.n_rows = len(d)
        self._times_ns = np.sort(d["_t"].unique()).astype("datetime64[ns]") if len(d) else np.array([], "datetime64[ns]")
        self._contracts: dict = {}
        for (kind, strike, expiry), g in d.groupby(["kind", "strike", "_e"], sort=False):
            self._contracts[(kind, float(strike), pd.Timestamp(expiry).value)] = (
                g["_t"].to_numpy().astype("datetime64[ns]"), {c: g[c].to_numpy() for c in _COLS if c in g})
        self._by_snap: dict = {}
        for t, g in d.groupby("_t", sort=False):
            self._by_snap[pd.Timestamp(t).value] = {pd.Timestamp(e).value: np.sort(x["strike"].unique())
                                                    for e, x in g.groupby("_e")}

    @property
    def snapshot_times(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self._times_ns, tz="UTC")

    def snapshot_at(self, at) -> pd.Timestamp | None:
        """Latest snapshot taken at or before `at` and within `max_age_sec`; else None."""
        if not len(self._times_ns):
            return None
        a = _naive_utc(at)
        i = int(np.searchsorted(self._times_ns, np.datetime64(a, "ns"), side="right")) - 1
        if i < 0:
            return None
        t = pd.Timestamp(self._times_ns[i])
        return t.tz_localize("UTC") if a - t <= self.max_age else None

    def expiries(self, snap: pd.Timestamp) -> list[pd.Timestamp]:
        by = self._by_snap.get(_naive_utc(snap).value, {})
        return sorted(pd.Timestamp(v, tz="UTC") for v in by)

    def strikes(self, expiry: pd.Timestamp, snap: pd.Timestamp) -> np.ndarray:
        by = self._by_snap.get(_naive_utc(snap).value, {})
        return by.get(_naive_utc(expiry).value, np.array([]))

    def quote(self, kind: str, strike: float, expiry, at) -> RealQuote | None:
        """The latest recorded quote for this contract at or before `at`, no older than `max_age_sec`."""
        c = self._contracts.get((kind, float(strike), _naive_utc(expiry).value))
        if c is None:
            return None
        times, cols = c
        a = _naive_utc(at)
        i = int(np.searchsorted(times, np.datetime64(a, "ns"), side="right")) - 1
        if i < 0:
            return None
        t = pd.Timestamp(times[i])
        if a - t > self.max_age:
            return None
        g = lambda k: _f(cols[k][i]) if k in cols else None  # noqa: E731
        return RealQuote(t.tz_localize("UTC"), (a - t).total_seconds(), str(cols["tier"][i]), g("bid"), g("ask"), g("mark"),
                         g("mark_iv"), g("delta"), g("gamma"), g("theta"), g("vega"), g("open_interest"), g("volume"),
                         g("bid_size"), g("ask_size"), g("spot"), str(cols["symbol"][i]) if "symbol" in cols else "")

    def window(self) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if not len(self._times_ns):
            return None
        return pd.Timestamp(self._times_ns[0], tz="UTC"), pd.Timestamp(self._times_ns[-1], tz="UTC")
