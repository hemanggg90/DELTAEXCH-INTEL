"""
P3 research report: every strategy back-tested as real BTC/ETH option trades (hybrid model), with in-sample /
out-of-sample results, time folds, model-vs-real price validation, and a walk-forward test of the ranker against
random selection.

    python scripts/research_report.py --since 2025-12-01                # writes docs/research/P3_REPORT.md
    python scripts/research_report.py --since 2026-06-01 --max-points 300

Needs the caches built by fetch_candles.py and build_iv_history.py. Runs offline: it makes no network calls.
The report is written as measured, including when nothing works.
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

from delta_intelligence.backtesting.option_backtest import (  # noqa: E402
    OptionBacktestConfig, RealOptionPrices, run_option_backtest, split_in_out, summarize_trades, time_folds,
    validation_stats)
from delta_intelligence.backtesting.option_market import OptionMarketModel  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import get_watchlist, underlying_for  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.features.feature_engine import compute_features  # noqa: E402
from delta_intelligence.features.inputs import load_feature_inputs  # noqa: E402
from delta_intelligence.options.iv_history import IvHistoryStore, fit_smile  # noqa: E402
from delta_intelligence.ranking.evaluate_ranker import evaluate, summarize  # noqa: E402
from delta_intelligence.strategies.base import STRUCTURES, research_frame  # noqa: E402
from delta_intelligence.strategies.registry import get_all_strategies  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_market(store: IvHistoryStore, asset: str, index: pd.DataFrame) -> tuple[OptionMarketModel, RealOptionPrices]:
    meta = pd.read_parquet(store.meta_path())
    meta = meta[meta["underlying"] == asset]
    hourly = pd.read_parquet(store.root / "iv_obs" / f"{asset}_atm_hourly.parquet")
    obs = pd.read_parquet(store.obs_path(asset))
    smile = fit_smile(obs, hourly)
    strikes = {e: np.sort(g["strike"].unique()) for e, g in meta.groupby("expiry")}
    symbols = {(k, s, e): sym for k, s, e, sym in zip(meta["kind"], meta["strike"], meta["expiry"], meta["symbol"])}
    market = OptionMarketModel(asset, index, hourly, smile, strikes, symbols)

    def loader(expiry):
        p = store.expiry_path(asset, expiry)
        return pd.read_parquet(p) if p.exists() else None

    return market, RealOptionPrices(loader)


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    return f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-12-01")
    ap.add_argument("--max-points", type=int, default=800)
    ap.add_argument("--out", default=str(ROOT / "docs" / "research" / "P3_REPORT.md"))
    args = ap.parse_args()
    t0 = time.time()
    s = get_settings()
    dm = DataManager.from_settings(s)
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    since = dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
    report: dict = {"generated_utc": str(pd.Timestamp.now(tz="UTC")), "since": args.since, "underlyings": {}}
    md: list[str] = []

    for perp in get_watchlist():
        u = underlying_for(perp)
        print(f"{perp}: loading data ...", flush=True)
        inp = load_feature_inputs(dm, perp, "5m", since - dt.timedelta(days=3))
        feats = compute_features(inp.ohlcv, "5m", inp.aux)
        frame = research_frame(inp.ohlcv, feats)
        frame = frame[frame["timestamp"] >= pd.Timestamp(since)].reset_index(drop=True)
        feats = feats[feats["timestamp"] >= pd.Timestamp(since)].reset_index(drop=True)
        market, real = load_market(store, u.asset, inp.aux.index)
        rows, default_trades = [], {}
        for strat in get_all_strategies():
            setups = strat.historical_setups(frame)
            for policy in STRUCTURES:
                res = run_option_backtest(strat, frame, market, OptionBacktestConfig(policy=policy), real=real,
                                          setups=setups)
                ins, oos = split_in_out(res.trades)
                folds = [summarize_trades(f).get("net_expected_r") for f in time_folds(res.trades, 4)]
                row = {"strategy": strat.name, "structure": policy, "default": policy == strat.default_structure,
                       "setups": res.n_setups, "skipped": dict(res.skipped), **summarize_trades(res.trades),
                       "is_net_r": summarize_trades(ins).get("net_expected_r"),
                       "oos_net_r": summarize_trades(oos).get("net_expected_r"), "oos_n": len(oos),
                       "fold_net_r": folds, "validation": validation_stats(res.trades)}
                rows.append(row)
                if row["default"]:
                    default_trades[strat.name] = res.trades
            print(f"  {strat.name:32} setups {len(setups):5}  default-structure trades "
                  f"{len(default_trades.get(strat.name, [])):5}  ({time.time() - t0:.0f}s)", flush=True)
        print(f"{perp}: evaluating the ranker ...", flush=True)
        decisions = evaluate(default_trades, feats, u.asset, warmup_frac=0.4, max_points=args.max_points)
        ev = summarize(decisions)
        all_val = validation_stats([t for ts in default_trades.values() for t in ts])
        report["underlyings"][u.asset] = {"bars": len(frame), "strategies": rows, "ranker": ev, "validation": all_val}
        md += render(u.asset, frame, rows, ev, all_val)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    head = [f"# P3 research report: strategies as BTC/ETH option trades",
            "",
            f"Generated {report['generated_utc'][:16]} UTC from cached Delta Exchange India data since {args.since}. "
            "Produced by `scripts/research_report.py`; regenerate rather than edit.",
            "",
            "**How to read this:**",
            "- **Net R** = net USD P&L / the structure's max loss (premium or spread risk + entry fees). It is after "
            "modelled bid/ask spreads (75th-percentile live spreads), fees (0.01% capped at 3.5% of premium) and 18% "
            "GST (unverified).",
            "- **Entry and exit rules:** entry at the signal bar close; exit at the underlying stop (checked first), "
            "the target, 4 h, or 30 min before the 17:30 IST settlement.",
            "- **Pricing:** option prices are Black-Scholes at the as-of implied vol inferred from real Delta option "
            "trades.",
            "- **Validation** compares the model mid with real trades in the same contract and bar.",
            "- **Parameters** were fixed in advance; nothing was tuned on this data.",
            ""]
    out.write_text("\n".join(head + md), encoding="utf-8")
    (s.data_cache_dir / "research_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}  ({time.time() - t0:.0f}s)")
    return 0


def render(asset, frame, rows, ev, val) -> list[str]:
    md = [f"## {asset}", "",
          f"{len(frame):,} five-minute bars, {frame['timestamp'].iloc[0]:%d %b %Y} -> {frame['timestamp'].iloc[-1]:%d %b %Y}.",
          "",
          "### Default structure per strategy",
          "",
          "| Strategy | Structure | Trades | Days | Win % | Net R / trade | IS net R | OOS net R (n) | Folds (net R) | "
          "Underlying R | Fees / risk | Max DD (R) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted([r for r in rows if r["default"]], key=lambda r: -(r.get("net_expected_r") or -9)):
        if not r.get("n_trades"):
            md.append(f"| {r['strategy']} | {r['structure']} | 0 | - | - | - | - | - | - | - | - | - |")
            continue
        folds = " / ".join(fmt(f, 2) for f in r["fold_net_r"])
        md.append(f"| {r['strategy']} | {r['structure']} | {r['n_trades']} | {r['n_days']} | {r['win_rate']:.0%} | "
                  f"{fmt(r['net_expected_r'])} | {fmt(r['is_net_r'])} | {fmt(r['oos_net_r'])} ({r['oos_n']}) | {folds} | "
                  f"{fmt(r['underlying_expected_r'])} | {r['avg_fees_share_of_risk']:.1%} | {fmt(r['max_drawdown_r'], 1)} |")
    md += ["", "### All structures (net R per trade)", "", "| Strategy | LONG_OPTION | DEBIT_SPREAD | CREDIT_SPREAD |",
           "|---|---|---|---|"]
    by = {}
    for r in rows:
        by.setdefault(r["strategy"], {})[r["structure"]] = r
    for name, d in by.items():
        cells = [f"{fmt(d[p].get('net_expected_r'))} ({d[p].get('n_trades', 0)})" if p in d else "-" for p in STRUCTURES]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
    skipped = {}
    for r in rows:
        if r["default"]:
            for k, v in r["skipped"].items():
                skipped[k] = skipped.get(k, 0) + v
    md += ["", f"Setups skipped (default structures): {skipped or 'none'}. `no_iv` = no observed IV within 3 h; "
           "`overlap` = a trade was already open.", ""]
    md += ["### Model vs real option prices", ""]
    if val.get("n"):
        md.append(f"{val['n']} legs had a real trade in the same contract and 5-minute bar: median |model - real| = "
                  f"{val['median_abs_err_pct']:.1f}%, median bias {val['median_bias_pct']:+.1f}% "
                  f"(positive = model above real), p90 {val['p90_abs_err_pct']:.1f}%.")
    else:
        md.append("No overlapping real trades to validate against.")
    md += ["", "### Ranker vs random selection (walk-forward, only already-closed trades)", ""]
    if ev.get("decision_points"):
        md += [f"- Decision points: {ev['decision_points']} (NO TRADE at {ev['no_trade_points']}; ranker picked a "
               f"strategy without a setup at {ev['selected_but_no_setup']})",
               f"- Trades taken by the ranker: {ev['trades_taken']} over {ev.get('trade_days', 0)} days"]
        if ev.get("trades_taken"):
            md += [f"- Ranker's mean net R: {fmt(ev['selected_mean_net_r'])} (95% CI {ev['selected_mean_net_r_ci95']}), "
                   f"win rate {ev['selected_win_rate']:.0%}",
                   f"- Random pick at the same points: {fmt(ev['random_pick_mean_net_r'])}; lift "
                   f"{fmt(ev['lift_vs_random_pick'])} (95% CI {ev['lift_ci95']})"]
        md.append(f"- Taking every signal: {fmt(ev['take_every_signal_mean_net_r'])} net R per trade"
                  + (f"; trades NO TRADE avoided averaged {fmt(ev['avoided_by_no_trade_mean_net_r'])}"
                     if "avoided_by_no_trade_mean_net_r" in ev else ""))
    else:
        md.append("No decision points.")
    return md + [""]


if __name__ == "__main__":
    sys.exit(main())
