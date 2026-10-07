"""
Standalone option-chain recorder: records REAL option quotes without running the trading engine.

    python scripts/record_chain.py                         # BTC, ETH, XAUT every 300 s, until Ctrl+C
    python scripts/record_chain.py --every 60 --once       # one snapshot now and exit
    python scripts/record_chain.py --hours 12 --every 120  # run for 12 hours

Public market data only (no keys, no orders, no account access). It writes to the same database table the engine's
recorder uses (`chain_snapshots`), so the engine and this script can both feed one history; a (symbol, snapshot time)
pair is written once. The PC must stay awake while it runs. A gap in recording is a gap in the real data: the coverage
report shows it and the research never fills it.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delta_intelligence.brokers.delta_api_client import public_client  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.database import db  # noqa: E402
from delta_intelligence.execution import chain_recorder  # noqa: E402
from delta_intelligence.options.chain import fetch_chain  # noqa: E402
from delta_intelligence.utils.timeutil import now_utc  # noqa: E402


def snapshot_once(client, underlyings: tuple[str, ...]) -> dict:
    chain = fetch_chain(client, underlyings, max_age=0)
    spots: dict[str, float] = {}
    for q in chain.by_symbol.values():
        if q.spot:
            spots.setdefault(q.underlying, q.spot)
    n = chain_recorder.record(chain, spots, now_utc())
    return {"quotes_in_chain": len(chain.by_symbol), "rows_written": n, "underlyings": sorted(spots)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--underlyings", default="BTC,ETH,XAUT")
    ap.add_argument("--every", type=float, default=300.0, help="seconds between snapshots")
    ap.add_argument("--hours", type=float, default=0.0, help="stop after this many hours (0 = until Ctrl+C)")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    unds = tuple(u.strip().upper() for u in args.underlyings.split(",") if u.strip())
    db.init_db()
    client = public_client(get_settings())
    t_end = time.monotonic() + args.hours * 3600 if args.hours else None
    print(f"recording {', '.join(unds)} every {args.every:.0f}s into the chain_snapshots table (Ctrl+C to stop)")
    fails = 0
    while True:
        started = time.monotonic()
        try:
            r = snapshot_once(client, unds)
            fails = 0
            print(f"{now_utc():%Y-%m-%d %H:%M:%S}Z  wrote {r['rows_written']:5d} rows  ({r['quotes_in_chain']} quotes, "
                  f"{', '.join(r['underlyings'])})", flush=True)
        except Exception as exc:  # a failed snapshot is a gap, never a reason to stop or to invent data
            fails += 1
            print(f"{now_utc():%Y-%m-%d %H:%M:%S}Z  snapshot FAILED ({type(exc).__name__}: {exc}); gap #{fails}", flush=True)
        if args.once or (t_end and time.monotonic() >= t_end):
            return 0 if fails == 0 else 1
        time.sleep(max(1.0, args.every - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("stopped")
