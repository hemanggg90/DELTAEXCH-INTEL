"""
Write the small committed IV seed (the last ~75 days of hourly ATM IV per asset) from the local IV history, so a deployed copy
can show an IV percentile on day one.

    python scripts/build_iv_seed.py            # BTC, ETH, XAUT -> delta_intelligence/evidence/iv_seed_<ASSET>.parquet

Needs the local parquet from scripts/build_iv_history.py. Refresh it every few weeks: the percentile only uses the last 60
days, so a stale seed stops helping (the recorder's own snapshots then take over). Real data only; nothing is filled in.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import UNDERLYINGS  # noqa: E402
from delta_intelligence.options import iv_live  # noqa: E402


def main() -> int:
    s = get_settings()
    root = s.data_cache_dir / "options" / s.data_env.lower() / "iv_obs"
    iv_live.SEED_DIR.mkdir(parents=True, exist_ok=True)
    for u in UNDERLYINGS.values():
        p = root / f"{u.asset}_atm_hourly.parquet"
        if not p.exists():
            print(f"{u.asset}: no local IV history ({p}); skipped")
            continue
        h = pd.read_parquet(p).dropna(subset=["atm_iv_6_30h"])
        h = h[["available_at", "atm_iv_6_30h"]].copy()
        h["available_at"] = pd.to_datetime(h["available_at"], utc=True)
        h = h[h["available_at"] >= h["available_at"].max() - pd.Timedelta(days=75)].sort_values("available_at")
        h.to_parquet(iv_live.seed_path(u.asset), index=False)
        print(f"{u.asset}: {len(h)} hourly observations, {h['available_at'].min():%Y-%m-%d} to {h['available_at'].max():%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
