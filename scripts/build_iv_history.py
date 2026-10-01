"""
Build the historical implied-volatility dataset from real expired BTC/ETH option trades, and print a coverage report.

    python scripts/build_iv_history.py --since 2025-12-01            # full backfill (resumable; cached per expiry)
    python scripts/build_iv_history.py --days 14 --workers 4          # quick sample

Public data only (production market data). Uses the shared rate limiter.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from delta_intelligence.brokers.delta_api_client import DeltaClient, public_client  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import UNDERLYINGS  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.options import iv_history as ivh  # noqa: E402
from delta_intelligence.utils.timeutil import now_utc  # noqa: E402

INDEX_FOR = {u.asset: u.index_symbol for u in UNDERLYINGS.values()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help="YYYY-MM-DD (UTC)")
    p.add_argument("--days", type=int, help="alternative to --since: last N days")
    p.add_argument("--band", type=float, default=0.03)
    p.add_argument("--window-hours", type=float, default=32.0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--underlyings", default="BTC,ETH")
    args = p.parse_args()
    until = now_utc()
    since = (dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc) if args.since
             else until - dt.timedelta(days=args.days or 14))
    underlyings = tuple(s.strip().upper() for s in args.underlyings.split(","))
    s = get_settings()
    store = ivh.IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    client = public_client(s)
    dm = DataManager.from_settings(s)
    t0 = time.time()

    print(f"[1/5] index candles since {since:%Y-%m-%d} ...", flush=True)
    index = {}
    for u in underlyings:
        df, meta = dm.get_ohlcv(INDEX_FOR[u], ivh.TF, since - dt.timedelta(days=3), until)
        index[u] = df
        print(f"      {INDEX_FOR[u]}: {len(df)} bars, quality {meta['quality_status']}")

    print("[2/5] listing expired options ...", flush=True)
    meta = ivh.list_expired_options(client, underlyings, since)
    meta = meta[meta["expiry"] <= pd.Timestamp(until)]
    if store.meta_path().exists():  # merge: a run for some underlyings must not drop the others
        old = pd.read_parquet(store.meta_path())
        meta = pd.concat([old[~old["symbol"].isin(meta["symbol"])], meta], ignore_index=True)
    ivh.IvHistoryStore.write(meta, store.meta_path())
    meta = meta[meta["underlying"].isin(underlyings)]
    print(f"      {len(meta)} expired contracts, {meta.groupby(['underlying', 'expiry']).ngroups} expiries")

    selected = ivh.select_contracts(meta, index, args.band, args.window_hours)
    print(f"[3/5] fetching candles for {len(selected)} near-ATM contracts "
          f"(+-{args.band:.0%} of the index range over the last {args.window_hours:.0f} h) ...", flush=True)

    def progress(i, n):
        if i % 10 == 0 or i == n:
            print(f"      {i}/{n} expiries  ({time.time() - t0:.0f}s)", flush=True)

    stats = ivh.fetch_expiry_candles(lambda: DeltaClient(client.base_url, environment=s.data_env), store, selected,
                                     args.window_hours, args.workers, progress)
    print(f"      {stats}")

    print("[4/5] implied volatility ...", flush=True)
    report: dict = {"period": [str(since), str(until)], "band": args.band, "window_hours": args.window_hours,
                    "fetch": stats, "underlyings": {}}
    for u in underlyings:
        files = sorted((store.root / "candles" / u).glob("*.parquet"))
        frames = [pd.read_parquet(f) for f in files]
        candles = pd.concat([f for f in frames if len(f)], ignore_index=True) if frames else pd.DataFrame()
        if len(candles):
            candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
            candles = candles[candles["timestamp"] >= pd.Timestamp(since) - pd.Timedelta(days=2)]
        obs = ivh.compute_iv_observations(candles, meta, index[u]) if len(candles) else pd.DataFrame()
        if len(obs):
            ivh.IvHistoryStore.write(obs, store.obs_path(u))
        hourly = ivh.atm_iv_hourly(obs) if len(obs) else pd.DataFrame()
        if len(hourly):
            ivh.IvHistoryStore.write(hourly, store.root / "iv_obs" / f"{u}_atm_hourly.parquet")
        smile = ivh.fit_smile(obs, hourly) if len(obs) else {}
        settle = ivh.settlement_check(meta[meta["underlying"] == u], index)
        report["underlyings"][u] = summarise(u, candles, obs, hourly, smile, settle, index[u], since, until)

    print("[5/5] report")
    out = store.root / "iv_coverage.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print_report(report)
    print(f"\nSaved {out}  (total {time.time() - t0:.0f}s)")
    return 0


def summarise(u, candles, obs, hourly, smile, settle, index, since, until) -> dict:
    n_hours = int((pd.Timestamp(until) - pd.Timestamp(since)) / pd.Timedelta(hours=1))
    traded = int((candles["volume"].fillna(0) > 0).sum()) if len(candles) else 0
    r: dict = {"candle_bars": int(len(candles)), "traded_bars": traded, "iv_obs": int(len(obs)),
               "iv_ok_pct": round(float(obs["iv"].notna().mean() * 100), 1) if len(obs) else 0.0,
               "hours_in_period": n_hours, "buckets": {}, "smile": smile}
    for name, _, _ in ivh.T_BUCKETS_H:
        col = f"atm_iv_{name}"
        if len(hourly) and col in hourly:
            h = hourly.dropna(subset=[col])
            h = h[(h["hour"] >= pd.Timestamp(since)) & (h["hour"] < pd.Timestamp(until))]
            gaps = h["hour"].diff().dt.total_seconds().div(3600).fillna(1)
            days = h["hour"].dt.floor("1D")
            per_day = days.value_counts()
            n_days = max(1, int(np.ceil((pd.Timestamp(until) - pd.Timestamp(since)) / pd.Timedelta(days=1))))
            r["buckets"][name] = {
                "hours_with_atm_iv_pct": round(len(h) / max(1, n_hours) * 100, 1),
                "days_with_6plus_hours_pct": round(int((per_day >= 6).sum()) / n_days * 100, 1),
                "median_obs_per_hour": float(h[f"n_{name}"].median()) if len(h) else 0,
                "longest_gap_hours": float(gaps.max()) if len(h) else None,
                "median_atm_iv": round(float(h[col].median()), 3) if len(h) else None,
                "p10_p90_atm_iv": [round(float(h[col].quantile(q)), 3) for q in (0.1, 0.9)] if len(h) else None,
            }
    if len(settle):
        r["settlement_model"] = {"expiries": int(len(settle)),
                                 "median_abs_error_pct": round(float(settle["error_pct"].abs().median()), 4),
                                 "max_abs_error_pct": round(float(settle["error_pct"].abs().max()), 4)}
    col = "atm_iv_6_30h"
    if len(hourly) and col in hourly and len(index):
        idx = index.set_index("timestamp")["close"]
        rv = np.log(idx).diff().rolling(288, min_periods=144).std() * np.sqrt(365 * 288)
        rv_h = rv.resample("1h").last()
        h = hourly.dropna(subset=[col]).set_index("hour")[col]
        joined = pd.concat([h, rv_h.reindex(h.index)], axis=1, keys=["iv", "rv"]).dropna()
        if len(joined):
            ratio = joined["iv"] / joined["rv"]
            r["iv_to_rv_1d"] = {"median": round(float(ratio.median()), 2),
                                "share_above_1_5": round(float((ratio > 1.5).mean() * 100), 1),
                                "p10_p90": [round(float(ratio.quantile(q)), 2) for q in (0.1, 0.9)]}
    return r


def print_report(rep: dict) -> None:
    print(f"\nIV HISTORY COVERAGE  {rep['period'][0][:10]} -> {rep['period'][1][:16]} UTC   band +-{rep['band']:.0%}, "
          f"window {rep['window_hours']:.0f}h")
    for u, r in rep["underlyings"].items():
        print(f"\n{u}: {r['candle_bars']} option bars cached, {r['traded_bars']} traded, {r['iv_obs']} IV observations "
              f"({r['iv_ok_pct']}% inverted OK)")
        for name, b in r["buckets"].items():
            print(f"  ATM IV {name:>6} to expiry: hours covered {b['hours_with_atm_iv_pct']}%, days with >=6 h "
                  f"{b['days_with_6plus_hours_pct']}%, obs/hour {b['median_obs_per_hour']}, longest gap "
                  f"{b['longest_gap_hours']} h, median IV {b['median_atm_iv']} (p10-p90 {b['p10_p90_atm_iv']})")
        for name, sm in r["smile"].items():
            print(f"  smile {name:>6}: iv - atm = {sm['slope']:+.2f}*k {sm['curvature']:+.1f}*k^2  "
                  f"(n={sm['n']}, residual sd {sm['resid_std']:.3f})")
        if "settlement_model" in r:
            sm = r["settlement_model"]
            print(f"  settlement model (30-min TWAP of 5m index closes) vs Delta: median |err| "
                  f"{sm['median_abs_error_pct']}%, max {sm['max_abs_error_pct']}% over {sm['expiries']} expiries")
        if "iv_to_rv_1d" in r:
            x = r["iv_to_rv_1d"]
            print(f"  ATM IV (6-30h) / 24h realised vol: median {x['median']}, p10-p90 {x['p10_p90']}, "
                  f"above 1.5 (your debit-veto rule) {x['share_above_1_5']}% of hours")


if __name__ == "__main__":
    sys.exit(main())
