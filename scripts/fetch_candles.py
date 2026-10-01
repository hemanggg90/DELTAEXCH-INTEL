"""
Fetch public candles (and optionally MARK/FUNDING/OI series) into the Parquet cache, and print the quality report.

    python scripts/fetch_candles.py BTCUSD --timeframe 5m --days 60
    python scripts/fetch_candles.py BTCUSD ETHUSD --series FUNDING OI --series-timeframe 1h

Public data only: no keys are needed. The data environment is DELTA_DATA_ENV (PRODUCTION by default in PAPER mode).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.data.data_manager import DataManager, DataUnavailableError  # noqa: E402
from delta_intelligence.utils.timeutil import fmt_ist, now_utc  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--series", nargs="*", default=[], choices=["MARK", "FUNDING", "OI"])
    p.add_argument("--series-timeframe", default="1h")
    p.add_argument("--force", action="store_true", help="ignore the cache and refetch everything")
    args = p.parse_args()

    settings = get_settings()
    dm = DataManager.from_settings(settings)
    start = now_utc() - dt.timedelta(days=args.days)
    print(f"Data environment: {settings.data_env} ({settings.data_rest_base_url}); cache: {dm.cache_dir}\n")
    failed = False
    jobs = [(s, None, args.timeframe) for s in args.symbols]
    jobs += [(s, k, args.series_timeframe) for s in args.symbols for k in args.series]
    for symbol, kind, tf in jobs:
        label = f"{kind}:{symbol}" if kind else symbol
        try:
            if kind:
                df, meta = dm.get_series(kind, symbol, tf, start, force_refresh=args.force)
            else:
                df, meta = dm.get_ohlcv(symbol, tf, start, force_refresh=args.force)
        except DataUnavailableError as exc:
            print(f"[FAIL] {label} {tf}: {exc}")
            failed = True
            continue
        q = meta["quality_report"]
        print(f"[{meta['quality_status']:>8}] {label} {tf}: {meta['n_rows']} bars from {meta['source']}, "
              f"{fmt_ist(meta['start_ts'], '%d %b %Y %H:%M')} -> {fmt_ist(meta['end_ts'], '%d %b %Y %H:%M IST')}")
        for issue in q["issues"]:
            print(f"           - {issue}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
