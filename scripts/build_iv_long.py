"""
Extend the IV history to WEEKLY / MONTHLY options, which swing strategies need (2-4 week DTE).

Friday-expiry contracts trade for weeks. For each, this fetches 1h candles over [expiry − 35 d, expiry − 32 h] (the
last 32 h are already covered at 5m by build_iv_history.py), for strikes thinned to a ~1% grid within the index range.
It then computes IV per traded hour against the 1h index, merges that with the 5m observations, and rebuilds the hourly
ATM IV per time-to-expiry bucket (now including 54-168 h and 168-720 h).

    python scripts/build_iv_long.py --underlyings BTC,ETH

Run build_iv_history.py first (it lists expired contracts and caches the index). Public data only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from delta_intelligence.brokers.delta_api_client import DeltaClient, public_client  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import UNDERLYINGS  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.options import iv_history as ivh  # noqa: E402

INDEX_FOR = {u.asset: u.index_symbol for u in UNDERLYINGS.values()}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--underlyings", default="BTC,ETH")
    p.add_argument("--window-days", type=float, default=35.0)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    s = get_settings()
    store = ivh.IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    client = public_client(s)
    dm = DataManager.from_settings(s)
    meta = pd.read_parquet(store.meta_path())
    t0 = time.time()
    for u in (x.strip().upper() for x in args.underlyings.split(",")):
        m = meta[meta["underlying"] == u]
        if not len(m):
            print(f"{u}: no expired contracts listed (run build_iv_history.py first)")
            continue
        start = m["expiry"].min() - pd.Timedelta(days=args.window_days + 2)
        idx5, _ = dm.get_ohlcv(INDEX_FOR[u], "5m", start.to_pydatetime(), dt.datetime.now(dt.timezone.utc))
        sel = ivh.select_long_dated(m, {u: idx5}, args.window_days)
        print(f"{u}: {sel.groupby('expiry').ngroups} Friday expiries, {len(sel)} contracts to fetch (1h)", flush=True)
        stats = ivh.fetch_long_dated(lambda: DeltaClient(client.base_url, environment=s.data_env), store, sel,
                                     args.window_days, workers=args.workers,
                                     progress=lambda i, n: print(f"   {i}/{n} expiries ({time.time() - t0:.0f}s)",
                                                                 flush=True) if i % 5 == 0 or i == n else None)
        print(f"   {stats}")
        files = sorted((store.root / "candles_1h" / u).glob("*.parquet"))
        candles = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
        obs1h = ivh.compute_iv_observations(candles, m, ivh.index_hourly(idx5), tf_seconds=ivh.LONG_TF_SEC)
        obs5 = pd.read_parquet(store.obs_path(u)) if store.obs_path(u).exists() else pd.DataFrame()
        allobs = pd.concat([obs5, obs1h], ignore_index=True).sort_values("known_at").reset_index(drop=True)
        ivh.IvHistoryStore.write(allobs, store.obs_path(u))
        ivh.IvHistoryStore.write(obs1h, store.root / "iv_obs" / f"{u}_long_1h.parquet")
        hourly = ivh.atm_iv_hourly(allobs)
        ivh.IvHistoryStore.write(hourly, store.root / "iv_obs" / f"{u}_atm_hourly.parquet")
        n_hours = int((hourly["hour"].max() - hourly["hour"].min()) / pd.Timedelta(hours=1)) if len(hourly) else 1
        print(f"   1h IV obs {len(obs1h):,} ({obs1h['iv'].notna().mean():.1%} inverted OK)")
        for name, _, _ in ivh.T_BUCKETS_H:
            col = f"atm_iv_{name}"
            if col in hourly:
                h = hourly.dropna(subset=[col])
                print(f"   bucket {name:>9}: hours with ATM IV {len(h) / max(1, n_hours):.1%}, median {h[col].median():.3f}")
        smile = ivh.fit_smile(allobs, hourly)
        for name, sm in smile.items():
            print(f"   smile {name:>9}: a={sm['slope']:+.2f} b={sm['curvature']:+.1f} n={sm['n']}")
    print(f"done ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
