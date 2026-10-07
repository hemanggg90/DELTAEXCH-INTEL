"""
Walk-forward test of the LIVE ranker on its own evidence file: would it have picked better trades than chance?

    python scripts/evaluate_live_ranker.py                 # all assets, up to 600 decision points each
    python scripts/evaluate_live_ranker.py --max-points 0  # every decision point (slow)

At each timestamp where at least one strategy opened a trade (= had a setup), the ranker scores ONLY those strategies using only
trades that had already CLOSED (exit <= t and entry < t), exactly like the live engine, then selects one or NO TRADE. We compare
the selected strategy's realised net option R with the average of all strategies that had a setup then (a random pick) and report
how often it says NO TRADE. The evidence is MODELED (no recorded option-chain history yet). 95% intervals resample whole days.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from delta_intelligence.analogues.analogue_engine import COMPARISON_FEATURES  # noqa: E402
from delta_intelligence.ranking.evaluate_ranker import Decision, summarize  # noqa: E402
from delta_intelligence.ranking.live_ranker import LiveRanker  # noqa: E402


def evaluate_asset(ranker: LiveRanker, asset: str, warmup_frac: float = 0.4, max_points: int | None = 600) -> list[Decision]:
    ev = ranker.evidence[ranker.evidence["asset"] == asset]
    if ev.empty:
        return []
    times = sorted(ev["entry_timestamp"].unique())
    start = pd.Timestamp(times[int(len(times) * warmup_frac)])
    pts = [pd.Timestamp(t) for t in times if pd.Timestamp(t) >= start]
    if max_points and len(pts) > max_points:
        pts = pts[:: int(np.ceil(len(pts) / max_points))]
    by_time = {t: g for t, g in ev.groupby("entry_timestamp")}
    out = []
    for t in pts:
        g = by_time[t]
        names = list(g["strategy_name"].unique())
        current = g.iloc[0][COMPARISON_FEATURES].to_dict()  # the market state at that bar
        res = ranker.rank(asset, current, names, names, "OK", now=t)
        sel = res.selected
        r_at = g.groupby("strategy_name")["r_multiple"].mean()
        out.append(Decision(asset, t, str(t.date()), sel, len(names), float(r_at[sel]) if sel else None, float(r_at.mean())))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-points", type=int, default=600)
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "XAUT"])
    args = ap.parse_args()
    ranker = LiveRanker.from_file()
    if not ranker.available:
        print("no evidence file: run scripts/build_ranker_evidence.py first")
        return 1
    print(ranker.describe())
    for a in args.assets:
        d = evaluate_asset(ranker, a, max_points=args.max_points or None)
        if not d:
            print(f"{a}: no decision points")
            continue
        s = summarize(d)
        print(f"\n{a}: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
