"""
Features and regimes on real cached data: regime distribution, confidence, feature coverage, plus a no-look-ahead
re-check on the real candles.

    python scripts/regime_report.py --days 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from delta_intelligence.config.watchlist import get_watchlist  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.features.feature_engine import AuxData, compute_features  # noqa: E402
from delta_intelligence.features.inputs import load_feature_inputs  # noqa: E402
from delta_intelligence.market_state.market_state_engine import MIN_WARMUP_BARS, build_current_state  # noqa: E402
from delta_intelligence.regimes.regime_engine import REGIMES, classify_frame  # noqa: E402
from delta_intelligence.utils.timeutil import fmt_ist, now_utc  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--timeframe", default="5m")
    args = p.parse_args()
    dm = DataManager.from_settings()
    start = now_utc() - dt.timedelta(days=args.days)
    for symbol in get_watchlist():
        inp = load_feature_inputs(dm, symbol, args.timeframe, start)
        f = compute_features(inp.ohlcv, args.timeframe, inp.aux)
        body = f.iloc[MIN_WARMUP_BARS:]
        labels, probs = classify_frame(body)
        dist = pd.Series(labels).value_counts(normalize=True).reindex(REGIMES, fill_value=0)
        state = build_current_state(symbol, f, inp.quality_status)
        print(f"\n{symbol}: {len(f)} bars {fmt_ist(f['timestamp'].iloc[0], '%d %b')} -> "
              f"{fmt_ist(f['timestamp'].iloc[-1], '%d %b %H:%M IST')}, data quality {inp.quality_status}, "
              f"aux errors {inp.aux_errors or 'none'}")
        print(f"  regime share (argmax):  " + "  ".join(f"{r} {v:.0%}" for r, v in dist.items() if v > 0))
        print(f"  mean confidence {probs.max(axis=1).mean():.2f} (max prob of 10; 0.10 = no information)")
        print(f"  NOW: {state.regime} ({state.regime_confidence:.2f})")
        cov = body.drop(columns="timestamp").notna().mean()
        print(f"  features with <99% coverage after warm-up: "
              + (", ".join(f"{k} {v:.0%}" for k, v in cov.items() if v < 0.99) or "none"))
        # no-look-ahead re-check on real data: cut mid-history, recompute, compare every column
        cut = len(inp.ohlcv) - 1000
        cut_close = inp.ohlcv["timestamp"].iloc[cut - 1] + pd.Timedelta(args.timeframe.replace("m", "min"))

        def avail(df):
            return None if df is None else df[df["timestamp"] + pd.Timedelta("1h") <= cut_close]

        aux_cut = AuxData(index=None if inp.aux.index is None else inp.aux.index[inp.aux.index["timestamp"] < cut_close],
                          funding=avail(inp.aux.funding), oi=avail(inp.aux.oi))
        trunc = compute_features(inp.ohlcv.iloc[:cut].reset_index(drop=True), args.timeframe, aux_cut)
        try:
            pd.testing.assert_frame_equal(f.iloc[:cut].reset_index(drop=True), trunc, check_dtype=False, atol=1e-9)
            print(f"  no-look-ahead re-check on real data (cut at bar {cut}): PASS, all {f.shape[1] - 1} columns equal")
        except AssertionError as exc:
            print(f"  no-look-ahead re-check: FAIL - {str(exc).splitlines()[0]}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
