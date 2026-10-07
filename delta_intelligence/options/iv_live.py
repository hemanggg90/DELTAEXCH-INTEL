"""
ATM implied-volatility history for the IV percentile, from whatever REAL sources exist, never fabricated.

The percentile (`options.analytics.iv_percentile`) needs >= 100 observations of the ATM IV (6-30 h bucket) within 60 days.
The original code read them only from a local, git-ignored parquet built offline, so a deployed copy showed nothing. Sources,
merged in this order of preference:

1. the local parquet `data_cache/options/<env>/iv_obs/<ASSET>_atm_hourly.parquet` (hourly, if present);
2. a small committed seed `evidence/iv_seed_<ASSET>.parquet` (hourly, refreshed with `scripts/build_iv_seed.py`);
3. the recorder's own `chain_snapshots` (the ATM contracts' `mark_iv` each snapshot, 5-minute cadence).

Snapshots only add observations AFTER the last hourly one, so no time is counted twice. Every answer carries its source
and observation count; with too few observations the percentile is None and the message says how long until it exists.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from delta_intelligence.options.analytics import iv_percentile

SEED_DIR = Path(__file__).resolve().parents[1] / "evidence"
BUCKET_HOURS = (6.0, 30.0)  # same bucket as `atm_iv_6_30h`
MIN_OBS, LOOKBACK_DAYS = 100, 60


def seed_path(asset: str) -> Path:
    return SEED_DIR / f"iv_seed_{asset}.parquet"


def _series(df: pd.DataFrame, col: str = "atm_iv_6_30h", at: str = "available_at") -> pd.Series:
    h = df.dropna(subset=[col])
    return pd.Series(h[col].to_numpy(float), index=pd.to_datetime(h[at], utc=True)).sort_index()


def load_parquet_history(root: Path, asset: str) -> pd.Series | None:
    p = Path(root) / f"{asset}_atm_hourly.parquet"
    return _series(pd.read_parquet(p)) if p.exists() else None


def load_seed(asset: str) -> pd.Series | None:
    p = seed_path(asset)
    return _series(pd.read_parquet(p)) if p.exists() else None


def atm_iv_from_snapshots(snap: pd.DataFrame) -> pd.Series:
    """One ATM IV per snapshot: among contracts with 6-30 h to expiry take the nearest expiry and the strike closest to spot,
    and average the call and put `mark_iv`. `snap` needs taken_at, expiry, strike, kind, spot, mark_iv (UTC-aware times)."""
    if snap is None or snap.empty:
        return pd.Series(dtype=float)
    d = snap.dropna(subset=["mark_iv", "spot"]).copy()
    d["taken_at"] = pd.to_datetime(d["taken_at"], utc=True)
    d["expiry"] = pd.to_datetime(d["expiry"], utc=True)
    d["tte_h"] = (d["expiry"] - d["taken_at"]).dt.total_seconds() / 3600.0
    d = d[(d["tte_h"] >= BUCKET_HOURS[0]) & (d["tte_h"] <= BUCKET_HOURS[1]) & (d["mark_iv"] > 0)]
    if d.empty:
        return pd.Series(dtype=float)
    out = {}
    for t, g in d.groupby("taken_at"):
        g = g[g["expiry"] == g["expiry"].min()]
        k = g.loc[(g["strike"] - g["spot"]).abs().idxmin(), "strike"]
        out[t] = float(g[g["strike"] == k]["mark_iv"].mean())
    return pd.Series(out).sort_index()


def load_snapshot_rows(asset: str, since: pd.Timestamp) -> pd.DataFrame:
    """Only the columns and the near-the-money rows needed (read-only query of the project's database)."""
    from delta_intelligence.database import db
    from delta_intelligence.database.models import ChainSnapshot

    cutoff = pd.Timestamp(since).tz_convert("UTC").tz_localize(None)
    with db.get_session() as s:
        q = (s.query(ChainSnapshot.taken_at, ChainSnapshot.expiry, ChainSnapshot.strike, ChainSnapshot.kind, ChainSnapshot.spot,
                     ChainSnapshot.mark_iv)
             .filter(ChainSnapshot.underlying == asset, ChainSnapshot.taken_at >= cutoff, ChainSnapshot.mark_iv.isnot(None)))
        rows = [r for r in q.all() if r.spot and abs(r.strike / r.spot - 1) <= 0.03]
    return pd.DataFrame(rows, columns=["taken_at", "expiry", "strike", "kind", "spot", "mark_iv"])


def merge_history(hourly: list[pd.Series | None], snapshots: pd.Series | None) -> tuple[pd.Series, dict]:
    """Hourly sources first (earlier ones win on a duplicate time), then snapshots strictly after the last hourly value."""
    parts, used = [], []
    for name, s in zip(("local parquet", "committed seed"), hourly):
        if s is not None and len(s):
            parts.append(s)
            used.append(f"{name} ({len(s)})")
    base = pd.concat(parts) if parts else pd.Series(dtype=float)
    if len(base):
        base = base[~base.index.duplicated(keep="first")].sort_index()
    snap_used = 0
    if snapshots is not None and len(snapshots):
        snap = snapshots[snapshots.index > base.index.max()] if len(base) else snapshots
        if len(base) and len(snap):  # keep the hourly cadence of the history it joins: the LAST snapshot of each hour, at its
            snap = snap[~snap.index.floor("h").duplicated(keep="last")]  # true timestamp (never moved earlier: no look-ahead)
        snap_used = len(snap)
        base = pd.concat([base, snap]).sort_index() if len(base) else snap
        if snap_used:
            used.append(f"recorded snapshots ({snap_used})")
    return base, {"n_obs": int(len(base)), "sources": used, "last": None if base.empty else base.index.max()}


def merged_history(asset: str, parquet_root: Path | None = None, now: pd.Timestamp | None = None,
                   use_db: bool = True) -> tuple[pd.Series, dict]:
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(days=LOOKBACK_DAYS + 1)
    local = load_parquet_history(parquet_root, asset) if parquet_root is not None else None
    snaps = None
    if use_db:
        try:
            snaps = atm_iv_from_snapshots(load_snapshot_rows(asset, since))
        except Exception:  # no database yet: the answer is simply "fewer sources"
            snaps = None
    return merge_history([local, load_seed(asset)], snaps)


def iv_status(history: pd.Series, info: dict, current_iv: float | None, now: pd.Timestamp) -> dict:
    """Percentile of the current ATM IV plus an honest note about the data behind it."""
    pct = iv_percentile(current_iv, history, pd.Timestamp(now), LOOKBACK_DAYS, MIN_OBS) if current_iv else None
    n_win = 0
    if len(history):
        n_win = int(((history.index <= pd.Timestamp(now)) & (history.index > pd.Timestamp(now) - pd.Timedelta(days=LOOKBACK_DAYS))).sum())
    if current_iv is None or not np.isfinite(current_iv):
        note = "No live ATM IV (the option chain is unavailable)."
    elif pct is not None:
        note = f"{n_win} observations from {', '.join(info.get('sources', [])) or 'history'}."
    else:
        note = (f"IV history too short: {n_win}/{MIN_OBS} observations in the last {LOOKBACK_DAYS} days"
                + (f" (sources: {', '.join(info['sources'])})" if info.get("sources") else " (no history source found)")
                + ". Keep the engine or `scripts/record_chain.py` running to build it (about 8 hours at 5-minute snapshots).")
    return {"iv": current_iv, "percentile": pct, "n_obs": n_win, "sources": info.get("sources", []), "note": note}
