"""Run the whole 10-strategy research matrix and write the report.

    python research_lab/run_research.py                       # full run (BTC, ETH, XAUT; 5m and 15m)
    python research_lab/run_research.py --reuse               # re-render from cached stages (same protocol only)
    python research_lab/run_research.py --quick               # few random-control runs: a smoke run, NOT the real result

Reads only cached public market data (fetch it first, see research_lab/README.md). It never calls a trading endpoint and
has no broker code. Output goes to research_lab/reports/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "research_lab")):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    from lab import evaluate as E
    from lab.pipeline import run_pipeline
    from lab.protocol import protocol_hash
    from lab.report import render, to_json

    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "XAUT"], choices=["BTC", "ETH", "XAUT"])
    ap.add_argument("--timeframes", nargs="+", default=["5m", "15m"], choices=["5m", "15m"])
    ap.add_argument("--since", default="2025-12-01")
    ap.add_argument("--reuse", action="store_true", help="reuse cached stage results for the same protocol/window")
    ap.add_argument("--quick", action="store_true", help="20 control runs instead of 300 (smoke run only)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--variants", nargs="*", default=None, help="restrict to these variant ids (debugging)")
    ap.add_argument("--out-dir", default=str(ROOT / "research_lab" / "reports"))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    runs = 20 if args.quick else E.CONTROL_RUNS
    print(f"protocol hash {protocol_hash()}")
    res = run_pipeline(args.assets, tuple(args.timeframes), args.since, out_dir, reuse=args.reuse, workers=args.workers,
                       control_runs=runs, variant_ids=args.variants)
    md = render(res)
    suffix = "_QUICK" if args.quick or args.variants else ""
    (out_dir / f"STRATEGY_LAB_REPORT{suffix}.md").write_text(md, encoding="utf-8")
    (out_dir / f"results{suffix}.json").write_text(json.dumps(to_json(res), indent=2), encoding="utf-8")
    print(f"\nwrote {out_dir / f'STRATEGY_LAB_REPORT{suffix}.md'}\n")
    for name, s in res["summary"].items():
        print(f"  {s['status']:18} {name}  ({s['cells']} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
