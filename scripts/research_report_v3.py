"""
P3' research report: the v3 strategy library as BOUGHT options on BTC, ETH and XAUT, judged by the user's acceptance
rules.

A strategy is ACCEPTED only if ALL of these hold:
1. >= 200 trades in total (all underlyings);
2. positive out-of-sample net expectancy (pooled last 30% of each underlying's trades);
3. stable under small parameter changes: every ±20% perturbation of each key parameter keeps the pooled net
   expectancy positive;
4. positive net expectancy on >= 2 underlyings (each with >= 30 trades).

No parameter was tuned. Rejections are reported as they are.

    python scripts/research_report_v3.py --since 2025-12-01      # writes docs/research/P3V3_REPORT.md
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
from delta_intelligence.config.events import load_events  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import get_watchlist, underlying_for  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.features.feature_engine import compute_features  # noqa: E402
from delta_intelligence.features.inputs import load_feature_inputs  # noqa: E402
from delta_intelligence.options.iv_history import IvHistoryStore, fit_smile  # noqa: E402
from delta_intelligence.ranking.evaluate_ranker import evaluate, summarize  # noqa: E402
from delta_intelligence.strategies.context import build_strategy_frame  # noqa: E402
from delta_intelligence.strategies.registry import STRATEGY_CLASSES, get_all_strategies  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_TRADES, MIN_PER_UNDERLYING, MIN_UNDERLYINGS = 200, 30, 2


def load_market(store: IvHistoryStore, asset: str, index: pd.DataFrame):
    meta = pd.read_parquet(store.meta_path())
    meta = meta[meta["underlying"] == asset]
    hourly = pd.read_parquet(store.root / "iv_obs" / f"{asset}_atm_hourly.parquet")
    obs = pd.read_parquet(store.obs_path(asset), columns=["known_at", "t_hours", "log_moneyness", "iv"])
    smile = fit_smile(obs, hourly)
    strikes = {e: np.sort(g["strike"].unique()) for e, g in meta.groupby("expiry")}
    symbols = {(k, s, e): sym for k, s, e, sym in zip(meta["kind"], meta["strike"], meta["expiry"], meta["symbol"])}

    def loader(expiry):
        p = store.expiry_path(asset, expiry)
        return pd.read_parquet(p) if p.exists() else None

    return OptionMarketModel(asset, index, hourly, smile, strikes, symbols), RealOptionPrices(loader), hourly


def perturbations(strat_cls, base: dict) -> list[tuple[str, dict]]:
    out = []
    for k in strat_cls.key_parameters:
        for f in (0.8, 1.2):
            v = base[k] * f
            if isinstance(base[k], int) or float(base[k]).is_integer() and k in ("window", "min_squeeze_bars",
                                                                                   "max_ob_age", "max_fvg_age"):
                v = int(round(v)) if int(round(v)) != base[k] else base[k] + (1 if f > 1 else -1)
            out.append((f"{k}x{f}", {**base, k: v}))
    return out


def mean_r(trades) -> float | None:
    return float(np.mean([t.net_r for t in trades])) if trades else None


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    return f"{x:+.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-12-01")
    ap.add_argument("--max-points", type=int, default=600)
    ap.add_argument("--out", default=str(ROOT / "docs" / "research" / "P3V3_REPORT.md"))
    args = ap.parse_args()
    t0 = time.time()
    s = get_settings()
    dm = DataManager.from_settings(s)
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    since = dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
    events = load_events()
    per: dict = {name: {} for name in STRATEGY_CLASSES}  # strategy -> underlying -> results
    ranker: dict = {}
    for perp in get_watchlist():
        u = underlying_for(perp)
        print(f"{perp}: loading ...", flush=True)
        inp = load_feature_inputs(dm, perp, "5m", since - dt.timedelta(days=3))
        feats = compute_features(inp.ohlcv, "5m", inp.aux)
        market, real, hourly = load_market(store, u.asset, inp.aux.index)
        frame = build_strategy_frame(inp.ohlcv, feats, hourly, events)
        keep = frame["timestamp"] >= pd.Timestamp(since)
        frame = frame[keep].reset_index(drop=True)
        feats = feats[keep.to_numpy()].reset_index(drop=True)
        default_trades = {}
        for strat in get_all_strategies():
            cls = type(strat)
            res = run_option_backtest(strat, frame, market, OptionBacktestConfig(), real=real)
            ins, oos = split_in_out(res.trades)
            fr_market = market
            spread, tick = fr_market.spread, fr_market.tick
            fr_market.spread, fr_market.tick = (0.0, 0.0), 1e-9
            fr = run_option_backtest(strat, frame, fr_market, OptionBacktestConfig(
                commission_rate=0, premium_cap_rate=0, gst_rate=0, apply_breakeven_gate=False))
            fr_market.spread, fr_market.tick = spread, tick
            pert = {}
            for label, params in perturbations(cls, strat.parameters):
                pr = run_option_backtest(cls(params), frame, market, OptionBacktestConfig())
                pert[label] = (mean_r(pr.trades), len(pr.trades))
            per[strat.name][u.asset] = {
                "summary": summarize_trades(res.trades), "trades": res.trades, "oos": oos, "ins": ins,
                "folds": [mean_r(f) for f in time_folds(res.trades, 4)], "skipped": dict(res.skipped),
                "setups": res.n_setups, "frictionless": mean_r(fr.trades), "perturb": pert,
                "validation": validation_stats(res.trades)}
            default_trades[strat.name] = res.trades
            print(f"  {strat.name:30} setups {res.n_setups:5} trades {len(res.trades):5} "
                  f"net R {fmt(mean_r(res.trades))}  ({time.time() - t0:.0f}s)", flush=True)
        ranker[u.asset] = summarize(evaluate(default_trades, feats, u.asset, warmup_frac=0.4,
                                             max_points=args.max_points))
    verdicts = {name: verdict(d) for name, d in per.items()}
    md = render(per, verdicts, ranker, events, args.since)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    slim = {n: {u: {k: v for k, v in r.items() if k not in ("trades", "oos", "ins")} for u, r in d.items()}
            for n, d in per.items()}
    (s.data_cache_dir / "research_report_v3.json").write_text(
        json.dumps({"verdicts": verdicts, "per": slim, "ranker": ranker}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out} ({time.time() - t0:.0f}s)")
    for n, v in verdicts.items():
        print(f"  {n:30} {'ACCEPTED' if v['accepted'] else 'REJECTED'}: {'; '.join(v['reasons']) or 'all criteria met'}")
    return 0


def verdict(d: dict) -> dict:
    all_trades = [t for r in d.values() for t in r["trades"]]
    oos = [t for r in d.values() for t in r["oos"]]
    n = len(all_trades)
    oos_r = mean_r(oos)
    labels = set().union(*[r["perturb"].keys() for r in d.values()]) if d else set()
    pert_pooled = {}
    for lab in labels:
        num = sum((r["perturb"][lab][0] or 0) * r["perturb"][lab][1] for r in d.values() if lab in r["perturb"])
        den = sum(r["perturb"][lab][1] for r in d.values() if lab in r["perturb"])
        pert_pooled[lab] = num / den if den else None
    good_u = [u for u, r in d.items() if len(r["trades"]) >= MIN_PER_UNDERLYING and (mean_r(r["trades"]) or -1) > 0]
    reasons = []
    if n < MIN_TRADES:
        reasons.append(f"only {n} trades (< {MIN_TRADES})")
    if oos_r is None or oos_r <= 0:
        reasons.append(f"out-of-sample net R {fmt(oos_r)} not positive")
    bad = [lab for lab, v in pert_pooled.items() if v is None or v <= 0]
    if bad:
        reasons.append(f"not stable: {len(bad)}/{len(pert_pooled)} parameter perturbations have net R <= 0")
    if len(good_u) < MIN_UNDERLYINGS:
        reasons.append(f"positive on {len(good_u)} underlying(s) (need {MIN_UNDERLYINGS}, each >= {MIN_PER_UNDERLYING} trades)")
    return {"accepted": not reasons, "reasons": reasons, "n_trades": n, "oos_net_r": oos_r,
            "pooled_net_r": mean_r(all_trades), "positive_underlyings": good_u, "perturbations": pert_pooled}


def render(per, verdicts, ranker, events, since) -> list[str]:
    acc = [n for n, v in verdicts.items() if v["accepted"]]
    md = ["# P3' research report: v3 strategies as bought options (BTC, ETH, XAUT)", "",
          f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC from cached Delta Exchange India data since {since}. "
          "Produced by `scripts/research_report_v3.py`; regenerate rather than edit.", "",
          f"**Result: {len(acc)} of {len(verdicts)} strategies ACCEPTED** "
          f"({', '.join(acc) if acc else 'none'}).", "",
          "**How to read this:**",
          "- **Net R** = net USD P&L / (premium paid + entry fees), i.e. the max loss of a bought option. It is after "
          "modelled bid/ask (75th-percentile live spreads), fees (0.01%, capped at 3.5% of premium) and GST 18% "
          "(unverified).",
          "- **Entry:** at the signal bar close; ATM or 1-ITM call/put with |delta| 0.40-0.60; expiry with "
          "DTE >= 2.5x the expected hold.",
          "- **Exits:** underlying stop (checked first), target, -35% premium stop, time stop, or 2 h before expiry.",
          "- **Breakeven gate:** trades whose expected move did not cover extrinsic premium + spread + fees by 25% were "
          "skipped.",
          "- **Premium sources:** `MODEL_REAL_IV` = Black-Scholes at as-of ATM IV inferred from real Delta option trades "
          "(+ fitted smile); `MODEL_REAL_IV_ADJ_BUCKET` = same, but using the nearest maturity bucket's IV. There is no "
          "recorded chain history yet (the chain recorder starts in P5).",
          f"- **Events:** the events calendar has {len(events)} entries"
          + (" (none configured, so the Pre-Event Straddle cannot be tested)." if not len(events) else "."), "",
          "## Verdicts", "", "| Strategy | Verdict | Trades | Pooled net R | OOS net R | Positive on | Reasons |",
          "|---|---|---|---|---|---|---|"]
    for n, v in verdicts.items():
        md.append(f"| {n} | {'**ACCEPTED**' if v['accepted'] else 'rejected'} | {v['n_trades']} | {fmt(v['pooled_net_r'])} | "
                  f"{fmt(v['oos_net_r'])} | {', '.join(v['positive_underlyings']) or '-'} | {'; '.join(v['reasons']) or '-'} |")
    md += ["", "## Per underlying", ""]
    for n, d in per.items():
        md += [f"### {n}", "", "| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | "
               "Underlying R | Fees/risk | Premium sources | Skips |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for u, r in d.items():
            sm = r["summary"]
            if not sm.get("n_trades"):
                md.append(f"| {u} | {r['setups']} | 0 | - | - | - | - | - | {fmt(r['frictionless'])} | - | - | - | {r['skipped']} |")
                continue
            folds = " / ".join(fmt(x, 2) for x in r["folds"])
            md.append(f"| {u} | {r['setups']} | {sm['n_trades']} | {sm['win_rate']:.0%} | {fmt(sm['net_expected_r'])} | "
                      f"{fmt(mean_r(r['ins']))} | {fmt(mean_r(r['oos']))} ({len(r['oos'])}) | {folds} | "
                      f"{fmt(r['frictionless'])} | {fmt(sm['underlying_expected_r'])} | {sm['avg_fees_share_of_risk']:.1%} | "
                      f"{sm['premium_sources']} | {r['skipped']} |")
        pv = verdicts[n]["perturbations"]
        if pv:
            md.append("")
            md.append("Parameter perturbations (pooled net R): " + ", ".join(f"{k} {fmt(v)}" for k, v in sorted(pv.items())))
        md.append("")
    md += ["## Model vs real option prices", ""]
    for n, d in per.items():
        for u, r in d.items():
            v = r["validation"]
            if v.get("n"):
                md.append(f"- {n} / {u}: {v['n']} legs, median |model-real| {v['median_abs_err_pct']:.1f}%, bias "
                          f"{v['median_bias_pct']:+.1f}%, p90 {v['p90_abs_err_pct']:.1f}%")
    md += ["", "## Ranker vs random (walk-forward, only already-closed trades)", ""]
    for u, ev in ranker.items():
        if not ev.get("decision_points"):
            md.append(f"- {u}: no decision points")
            continue
        line = (f"- {u}: {ev['decision_points']} decision points, NO TRADE at {ev['no_trade_points']}, trades taken "
                f"{ev['trades_taken']}")
        if ev.get("trades_taken"):
            line += (f"; ranker net R {fmt(ev['selected_mean_net_r'])} (CI {ev['selected_mean_net_r_ci95']}) vs random "
                     f"{fmt(ev['random_pick_mean_net_r'])}; lift {fmt(ev['lift_vs_random_pick'])} (CI {ev['lift_ci95']})")
        md.append(line)
    return md + [""]


if __name__ == "__main__":
    sys.exit(main())
