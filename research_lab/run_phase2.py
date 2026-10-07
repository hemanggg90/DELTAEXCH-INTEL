"""Phase 2: option-selection research on the frozen Phase 1 strategies, with real recorded option quotes wherever they exist.

    python research_lab/run_phase2.py                 # full run (cells with >= 200 Phase-1 trades, all policies)
    python research_lab/run_phase2.py --reuse         # re-render from cached stages (same protocol and window only)
    python research_lab/run_phase2.py --quick         # smoke run: 20 control runs, writes *_QUICK files

Read-only: public market data caches and the recorded chain table. No orders, no broker code. Output goes to
research_lab/reports/phase2/.
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
    from lab.protocol import protocol_hash
    from phase2 import pipeline2
    from phase2.protocol2 import protocol2_hash

    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "XAUT"], choices=["BTC", "ETH", "XAUT"])
    ap.add_argument("--timeframes", nargs="+", default=["5m", "15m"], choices=["5m", "15m"])
    ap.add_argument("--since", default="2025-12-01")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cells", nargs="*", default=None, help="restrict to cells like S2-n40:5m (debugging)")
    ap.add_argument("--policies", nargs="*", default=None)
    ap.add_argument("--out-dir", default=str(ROOT / "research_lab" / "reports" / "phase2"))
    args = ap.parse_args()

    frozen = (ROOT / "research_lab" / "FROZEN_PROTOCOL_HASH.txt").read_text().strip()
    if protocol_hash() != frozen:
        print("ABORT: the Phase 1 strategies/gates no longer match the frozen Phase 1 protocol hash.")
        return 2
    print(f"phase 1 hash verified {frozen[:12]}...; phase 2 hash {protocol2_hash()}")
    out_dir = Path(args.out_dir)
    cells = [tuple(c.split(":")) for c in args.cells] if args.cells else None
    res = pipeline2.run(args.assets, tuple(args.timeframes), args.since, out_dir,
                        ROOT / "research_lab" / "reports" / "results.json", reuse=args.reuse, workers=args.workers,
                        control_runs=20 if args.quick else E.CONTROL_RUNS, cells=cells, policy_ids=args.policies)
    return _write(res, out_dir, args)


def _write(res, out_dir: Path, args) -> int:
    from phase2 import report2

    suffix = "_QUICK" if args.quick or args.cells or args.policies else ""
    md = report2.render(res)
    (out_dir / f"PHASE2_REPORT{suffix}.md").write_text(md, encoding="utf-8")
    (out_dir / f"results{suffix}.json").write_text(json.dumps(report2.to_json(res), indent=2), encoding="utf-8")
    print(f"\nwrote {out_dir / f'PHASE2_REPORT{suffix}.md'}")
    from collections import Counter

    print(dict(Counter(c["status"] for c in res["cells"].values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
