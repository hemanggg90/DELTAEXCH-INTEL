"""
Data quality and coverage for RECORDED option-chain snapshots (REAL_RECORDED data).

`assess` flags every snapshot row and gives each a pricing tier. Flags never change or delete the recorded data; they
only decide how research may use it.

Tiers (what a row can be used for):
- `REAL_QUOTE`: two-sided, uncrossed, positive bid and ask, from a fresh exchange quote. Long-option entry may use the
  ask and exit the bid.
- `REAL_MARK_ONLY`: a positive mark but no usable two-sided quote. It is NOT a bid or an ask and is never silently
  treated as one.
- `UNUSABLE`: nothing usable (non-positive prices, crossed, missing).

Flags: DUPLICATE, STALE_QUOTE, CROSSED, NONPOSITIVE_PRICE, EXTREME_SPREAD, MISSING_GREEKS, IMPOSSIBLE_DTE,
EXPIRY_MISMATCH, ILLIQUID, ONE_SIDED. Snapshot-level: timestamp gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.options.chain import expiry_from_symbol

REAL_QUOTE, REAL_MARK_ONLY, UNUSABLE = "REAL_QUOTE", "REAL_MARK_ONLY", "UNUSABLE"
FLAGS = ("DUPLICATE", "STALE_QUOTE", "CROSSED", "NONPOSITIVE_PRICE", "EXTREME_SPREAD", "MISSING_GREEKS",
         "IMPOSSIBLE_DTE", "EXPIRY_MISMATCH", "ILLIQUID", "ONE_SIDED")
GREEKS = ("delta", "gamma", "theta", "vega")


@dataclass(frozen=True)
class QualityConfig:
    """Thresholds are configurable; the defaults are deliberately simple and documented in the Phase 2 report."""
    max_quote_age_sec: float = 600.0  # exchange quote timestamp older than this at snapshot time = stale
    extreme_spread_pct: float = 50.0  # (ask - bid) / mid above this is flagged (still a real quote)
    min_open_interest: float = 100.0  # same floor as the risk engine's open-interest check
    min_quote_size: float = 10.0  # same floor as the risk engine's quote-size check
    max_dte_days: float = 46.0  # the recorder keeps expiries up to 45 days out
    dte_tolerance_days: float = 0.01  # stored DTE vs (expiry - taken_at)
    gap_factor: float = 2.5  # a snapshot gap above gap_factor x the median interval is a gap
    min_gap_sec: float = 600.0  # ... and above this many seconds, so a 1-minute cadence never over-reports


@dataclass
class QualityReport:
    n_rows: int = 0
    n_snapshots: int = 0
    flag_counts: dict = field(default_factory=dict)
    tier_counts: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)  # (underlying, from, to, seconds)
    median_interval_sec: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def pct(self, flag: str) -> float:
        return 100.0 * self.flag_counts.get(flag, 0) / self.n_rows if self.n_rows else 0.0


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """UTC-aware timestamps and numeric columns. Does not drop or modify any recorded value."""
    d = df.copy()
    d["taken_at"] = _ts(d["taken_at"])
    d["expiry"] = _ts(d["expiry"])
    for c in ("bid", "ask", "mark", "bid_size", "ask_size", "open_interest", "volume", "spot", "strike", "dte_days",
              "quote_ts_us", *GREEKS, "mark_iv"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def assess(df: pd.DataFrame, cfg: QualityConfig | None = None) -> tuple[pd.DataFrame, QualityReport]:
    """Add boolean flag columns, a `tier`, and per-row `spread_pct`/`mid`. Returns (frame, report)."""
    cfg = cfg or QualityConfig()
    d = prepare(df)
    rep = QualityReport(n_rows=len(d))
    if d.empty:
        return d.assign(tier=[], spread_pct=[], mid=[]), rep
    bid, ask, mark = d["bid"], d["ask"], d["mark"]
    pos_bid, pos_ask = bid > 0, ask > 0
    two_sided = pos_bid & pos_ask
    d["DUPLICATE"] = d.duplicated(subset=["symbol", "taken_at"], keep="first")
    quote_t = pd.to_datetime(d["quote_ts_us"], unit="us", utc=True)
    age = (d["taken_at"] - quote_t).dt.total_seconds()
    d["quote_age_sec"] = age
    d["STALE_QUOTE"] = (age > cfg.max_quote_age_sec) | (age < -60)  # a quote from the FUTURE is a clock fault
    d["CROSSED"] = two_sided & (bid > ask)
    d["NONPOSITIVE_PRICE"] = (bid <= 0) | (ask <= 0) | (mark <= 0)
    d["mid"] = np.where(two_sided, (bid + ask) / 2, np.nan)
    d["spread_pct"] = np.where(two_sided & ~d["CROSSED"], (ask - bid) / d["mid"] * 100, np.nan)
    d["EXTREME_SPREAD"] = d["spread_pct"] > cfg.extreme_spread_pct
    d["MISSING_GREEKS"] = d[[g for g in GREEKS if g in d]].isna().any(axis=1) if any(g in d for g in GREEKS) else True
    recomputed = (d["expiry"] - d["taken_at"]).dt.total_seconds() / 86400.0
    dte = d["dte_days"].where(d["dte_days"].notna(), recomputed)
    d["IMPOSSIBLE_DTE"] = (dte <= 0) | (dte > cfg.max_dte_days) | ((d["dte_days"] - recomputed).abs() > cfg.dte_tolerance_days)
    parsed = d["symbol"].map(lambda s: _safe_expiry(s))
    d["EXPIRY_MISMATCH"] = parsed.isna() | (parsed != d["expiry"])
    d["ILLIQUID"] = (d["open_interest"].fillna(0) < cfg.min_open_interest) | (
        d[["bid_size", "ask_size"]].fillna(0).min(axis=1) < cfg.min_quote_size)
    d["ONE_SIDED"] = (pos_bid ^ pos_ask)
    usable_quote = two_sided & ~d["CROSSED"] & ~d["STALE_QUOTE"] & ~d["DUPLICATE"]
    d["tier"] = np.where(usable_quote, REAL_QUOTE, np.where((mark > 0) & ~d["DUPLICATE"], REAL_MARK_ONLY, UNUSABLE))
    rep.flag_counts = {f: int(d[f].sum()) for f in FLAGS}
    rep.tier_counts = {t: int((d["tier"] == t).sum()) for t in (REAL_QUOTE, REAL_MARK_ONLY, UNUSABLE)}
    rep.n_snapshots = int(d.groupby("underlying")["taken_at"].nunique().sum())
    rep.gaps, rep.median_interval_sec = snapshot_gaps(d, cfg)
    if rep.n_snapshots < 20:
        rep.notes.append(f"only {rep.n_snapshots} snapshots: too little for any statistical conclusion")
    return d, rep


def _safe_expiry(sym: str):
    try:
        return expiry_from_symbol(sym)
    except Exception:
        return pd.NaT


def snapshot_gaps(d: pd.DataFrame, cfg: QualityConfig) -> tuple[list, dict]:
    gaps, med = [], {}
    for u, g in d.groupby("underlying"):
        t = pd.DatetimeIndex(sorted(pd.to_datetime(g["taken_at"], utc=True).unique()))
        if len(t) < 2:
            med[u] = None
            continue
        dt_s = np.asarray((t[1:] - t[:-1]).total_seconds(), float)  # seconds between consecutive distinct snapshots
        m = float(np.median(dt_s))
        med[u] = m
        thr = max(cfg.gap_factor * m, cfg.min_gap_sec)
        for a, b, s in zip(t[:-1], t[1:], dt_s):
            if s > thr:
                gaps.append((u, a, b, float(s)))
    return gaps, med


# ---- coverage ------------------------------------------------------------------------------------------------------
def moneyness_region(kind: pd.Series, strike: pd.Series, spot: pd.Series) -> pd.Series:
    """ITM/OTM and distance buckets, from the strike's distance to spot (signed so ITM is positive)."""
    itm = np.where(kind == "C", spot > strike, spot < strike)
    dist = (strike / spot - 1.0).abs() * 100
    bucket = pd.cut(dist, [-0.001, 1.0, 3.0, 6.0, 100.0], labels=["ATM<1%", "1-3%", "3-6%", ">6%"])
    side = np.where(dist < 1.0, "", np.where(itm, "ITM ", "OTM "))
    return pd.Series([f"{s}{b}" for s, b in zip(side, bucket)], index=kind.index)


def dte_bucket(dte: pd.Series) -> pd.Series:
    return pd.cut(dte, [-1, 1, 3, 7, 14, 30, 100], labels=["<1d", "1-3d", "3-7d", "7-14d", "14-30d", ">30d"])


def coverage(d: pd.DataFrame, bar_minutes: int = 5) -> dict[str, pd.DataFrame]:
    """How much REAL option data exists, by asset/date, expiry, option type, strike region, DTE and timeframe.
    `d` is the output of `assess`. All counts are recorded rows; nothing is estimated or filled."""
    if d.empty:
        return {k: pd.DataFrame() for k in ("by_day", "by_expiry", "by_kind", "by_region", "by_dte", "by_timeframe")}
    x = d.assign(date=d["taken_at"].dt.strftime("%Y-%m-%d"), real_quote=(d["tier"] == REAL_QUOTE),
                 region=moneyness_region(d["kind"], d["strike"], d["spot"]), dteb=dte_bucket(d["dte_days"]))

    def agg(keys):
        g = x.groupby(keys, observed=True)
        out = g.agg(rows=("symbol", "size"), snapshots=("taken_at", "nunique"), contracts=("symbol", "nunique"),
                    real_quote_rows=("real_quote", "sum"))
        out["real_quote_pct"] = (100 * out["real_quote_rows"] / out["rows"]).round(1)
        return out.reset_index()

    by_day = agg(["underlying", "date"])
    by_expiry = agg(["underlying", x["expiry"].dt.strftime("%Y-%m-%d").rename("expiry_date")])
    tf = x.groupby(["underlying", "date"]).agg(snapshots=("taken_at", "nunique"), first=("taken_at", "min"),
                                                last=("taken_at", "max")).reset_index()
    span_bars = ((tf["last"] - tf["first"]).dt.total_seconds() / 60.0 / bar_minutes + 1).clip(lower=1)
    tf["bars_with_a_snapshot_pct"] = (100 * tf["snapshots"] / span_bars).clip(upper=100).round(1)
    tf["timeframe"] = f"{bar_minutes}m bars"
    return {"by_day": by_day, "by_expiry": by_expiry, "by_kind": agg(["underlying", "kind"]),
            "by_region": agg(["underlying", "region"]), "by_dte": agg(["underlying", "dteb"]),
            "by_timeframe": tf.drop(columns=["first", "last"])}


def load_snapshots(since=None, until=None, underlying: str | None = None) -> pd.DataFrame:
    """Recorded rows from the project's database as a DataFrame (UTC-aware times). Read-only."""
    from delta_intelligence.database import db
    from delta_intelligence.database.models import ChainSnapshot

    with db.get_session() as s:
        q = s.query(ChainSnapshot)
        if since is not None:
            q = q.filter(ChainSnapshot.taken_at >= pd.Timestamp(since).tz_convert("UTC").tz_localize(None))
        if until is not None:
            q = q.filter(ChainSnapshot.taken_at <= pd.Timestamp(until).tz_convert("UTC").tz_localize(None))
        if underlying:
            q = q.filter(ChainSnapshot.underlying == underlying)
        rows = [{c.name: getattr(r, c.name) for c in ChainSnapshot.__table__.columns} for r in q.order_by(ChainSnapshot.taken_at)]
    return prepare(pd.DataFrame(rows)) if rows else pd.DataFrame(columns=[c.name for c in ChainSnapshot.__table__.columns])
