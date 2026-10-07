"""Markdown + JSON report. Failures are shown as prominently as passes; nothing is selected or hidden."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from lab import evaluate as E
from lab import metrics as M
from lab.library import BUCKETS, DTE_MULTIPLES, LEGACY, VARIANTS, strategies_in_order, variant_by_id
from lab.pipeline import LEGACY_GROUP, strategy_of
from lab.protocol import protocol_dict, protocol_hash

fmt = E.fmt

# The project's earlier result for the same Supertrend variants, quoted verbatim from docs/research/STRATEGY_SEARCH.md
# (discovery 2025-12-01..2026-06-30 on BTC+ETH; holdout 2026-07-01+ on BTC, ETH, XAUT). Preserved for comparison.
OLD_SUPERTREND = [
    ("Supertrend Trend Following | tight | ATM | hold x1", "89, 49%, +0.176, -2.1", "33, 48%, +0.075, -2.9",
     "BTC +0.174 (16) / ETH +0.005 (16) / XAUT -0.410 (1)"),
    ("Supertrend Trend Following | tight | ITM1 | hold x1", "133, 46%, +0.088, -3.3", "47, 51%, +0.069, -3.7",
     "BTC +0.167 (22) / ETH -0.001 (24) / XAUT -0.410 (1)"),
]


def _label(vid: str) -> str:
    return vid


def _pooled_all(cell):
    return E.pooled(cell["assets"])


def _cell_row(key, cell, verdict) -> str:
    vid, tf = key
    trades = _pooled_all(cell)
    disc, hold = E.split_holdout(trades)
    per_asset = " / ".join(f"{a} {fmt(E.mean_r(r['trades']))} ({len(r['trades'])})" for a, r in cell["assets"].items())
    folds = " / ".join(fmt(x, 2) for x in M.fold_expectancies(trades, E.N_FOLDS))
    gates = verdict["gates"]
    gate_txt = " ".join(f"{k}:{'ok' if v['ok'] else 'NO' if v['ok'] is False else '-'}"
                        for k, v in gates.items() if k not in ("trades", "holdout_n", "holdout_r"))
    return (f"| {vid} | {tf} | **{verdict['status']}** | {len(trades)} | {fmt(E.mean_r(trades))} | "
            f"{fmt(E.mean_r(disc))} ({len(disc)}) | {fmt(E.mean_r(hold))} ({len(hold)}) | {folds} | {per_asset} | "
            f"{gate_txt} | {'; '.join(verdict['reasons']) or '-'} |")


def render(res: dict, generated: datetime | None = None) -> str:
    cells, verdicts, summary, info = res["cells"], res["verdicts"], res["summary"], res["info"]
    now = generated or datetime.now(timezone.utc)
    accepted = [n for n, s in summary.items() if s["status"] == E.ACCEPTED and n != LEGACY_GROUP]
    counts = {s: sum(1 for n, v in summary.items() if v["status"] == s and n != LEGACY_GROUP) for s in E.STATUS_ORDER}
    md: list[str] = []
    md += ["# Strategy library research report: 10 long-option strategies on BTC, ETH and XAUT", "",
           f"Generated {now:%Y-%m-%d %H:%M} UTC by `research_lab/run_research.py`. Protocol version "
           f"{protocol_dict()['version']}, protocol hash `{protocol_hash()}`. Data window from {res['since']}; signal "
           f"timeframes {', '.join(res['timeframes'])}.", "",
           f"## Result: {len(accepted)} of {len(strategies_in_order())} strategies ACCEPTED"
           + (f" ({', '.join(accepted)})" if accepted else ""), "",
           "Strategy-level status is the BEST status among that strategy's tested cells (variant x timeframe). "
           "Reading it alone flatters a strategy that was given several chances, which is why the acceptance gate "
           "includes a Bonferroni adjustment over those cells. The full cell table below is the real result.", "",
           "Status counts (strategies): " + ", ".join(f"{k} {v}" for k, v in counts.items()), "",
           "| Strategy | Status | Cells tested | " + " | ".join(E.STATUS_ORDER) + " |",
           "|---|---|---|" + "---|" * len(E.STATUS_ORDER)]
    for name, s in summary.items():
        md.append(f"| {name} | **{s['status']}** | {s['cells']} | " + " | ".join(str(s["by_status"][k]) for k in E.STATUS_ORDER) + " |")
    if res["errors"]:
        md += ["", "**Run errors (these assets or stages did not complete):**", ""]
        md += [f"- {a}: {e}" for a, e in res["errors"].items()]
    md += _methodology(res)
    md += _datasets(info)
    md += _ranges()
    md += ["", "## All cells: status and why", "",
           "Net R = net P&L / (premium paid + entry fees), after the project's modelled bid/ask spread, fees "
           "(0.01% of notional, capped at 3.5% of premium) and 18% GST (unverified), on BOTH legs for straddles. "
           "Holdout = trades entered on or after 2026-07-01 (never used to choose anything). Folds = mean net R in 4 "
           "consecutive time slices. Gate letters: perturbation, assets, buckets, control (ok / NO / - = not run).", "",
           "| Variant | TF | Status | Trades | Pooled net R | Discovery net R (n) | Holdout net R (n) | Folds | "
           "Per asset net R (n) | Gates | Reasons |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(cells, key=lambda k: (strategy_of(k[0]), k[0], k[1])):
        md.append(_cell_row(key, cells[key], verdicts[key]))
    md += _gate_details(cells, verdicts)
    md += _metrics_section(cells)
    md += _regimes(cells)
    md += _straddle(cells)
    md += _cost_section(cells)
    md += _supertrend(cells, verdicts)
    md += _limitations(res)
    return "\n".join(md) + "\n"


def _methodology(res) -> list[str]:
    return ["", "## Methodology", "",
            "- **Question:** does any of these 10 hypotheses have a robust edge after realistic option costs? The method "
            "was frozen before the first run (hash above); no parameter was changed after seeing a result.",
            "- **Signals** are generated on the underlying's perpetual from closed bars only (no look-ahead; unit tests "
            "prove it by truncation). Warm-up or missing inputs give NO SIGNAL.",
            "- **Option selection is separate** and is the project's own: the chosen contract is priced by the project's "
            "option market model (Black-Scholes at the as-of ATM IV inferred from real Delta option trades, plus a fitted "
            "smile), entered at the modelled ask, exited at the modelled bid, with the breakeven gate, -35% premium stop, "
            "time stop, underlying stop/target and the 2 h expiry guard. One trade at a time per strategy.",
            "- **Option buckets** (project selector reaches ATM and 1-ITM only): "
            + ", ".join(f"`{k}` ({b.moneyness}, |delta| {b.delta_min}-{b.delta_max})" for k, b in BUCKETS.items())
            + f"; DTE multiples {', '.join(str(d) for d in DTE_MULTIPLES)}. The baseline bucket is "
            "`ATM_040_060` at 2.5x; the others are consistency checks, never chosen per strategy.",
            "- **No fitting happens anywhere**, so walk-forward is frozen parameters evaluated forward: discovery "
            f"before {E.HOLDOUT_START:%Y-%m-%d}, a frozen holdout after it, and 4 consecutive time folds. Parameters "
            "are shared across assets; per-asset results are reported, never tuned.",
            f"- **Gates:** >= {E.MIN_TRADES} pooled trades (else DATA-INSUFFICIENT, never lowered); >= {E.MIN_HOLDOUT} "
            f"holdout trades; holdout net R > 0 (else REJECTED); every +-20% perturbation of each key parameter keeps "
            f"pooled net R > 0; >= {E.MIN_ASSETS} assets with >= {E.MIN_PER_ASSET} trades and positive net R; positive "
            f"holdout net R in >= {E.MIN_BUCKETS_POSITIVE} of {len(BUCKETS)} option buckets; and beating "
            f"{res['control_runs']} random-signal runs (same entry times, selector and costs; directions flipped at "
            f"random; straddles use random entry times) with a Bonferroni-adjusted p <= {E.CONTROL_ALPHA}. "
            "Cells that pass the earlier gates but not all of them are EXPERIMENTAL.",
            "- **Staged runs:** perturbation/bucket runs happen only for cells with >= 200 trades and a positive holdout, "
            "and the random control only for cells that passed every other gate. A cell that failed an earlier gate "
            "cannot change status, so running the rest would only cost time. 'not run' means exactly that."]


def _datasets(info) -> list[str]:
    md = ["", "## Datasets", "",
          "| Asset | Timeframe | Bars | From | To | Funding z coverage | OI z coverage | Index coverage | IV pct coverage |",
          "|---|---|---|---|---|---|---|---|---|"]
    for a, i in info.items():
        for tf in [k for k in i if k in ("5m", "15m")]:
            c = i[tf]
            md.append(f"| {a} | {tf} | {c['bars']} | {str(c['start'])[:10]} | {str(c['end'])[:10]} | {c['funding_z']:.0%} | "
                      f"{c['oi_z']:.0%} | {c['index_close']:.0%} | {c['iv_percentile']:.0%} |")
    md += ["", "Listed option expiries available for pricing:", ""]
    for a, i in info.items():
        o = i["options"]
        md.append(f"- {a}: {o['expiries']} expiries, {o['first_expiry'][:10]} to {o['last_expiry'][:10]}"
                  + (f"; auxiliary data errors: {i['aux_errors']}" if i.get("aux_errors") else ""))
    return md


def _ranges() -> list[str]:
    md = ["", "## Parameters and research ranges", "",
          "Each variant is a pre-declared hypothesis; the +-20% perturbation of each key parameter is the only local "
          "search, and it is used for robustness, not selection.", "",
          "| Variant | Strategy | Parameters | Key parameters perturbed +-20% |", "|---|---|---|---|"]
    for v in VARIANTS:
        d = {k: x for k, x in v.config.as_dict().items()}
        md.append(f"| {v.id} | {v.strategy_name} | `{json.dumps(d)}` | {', '.join(v.config.key_params)} |")
    md += ["", "Legacy variants (the project's own `SupertrendFlip`, target 2.25 ATR, hold 2h/4h): "
           + ", ".join(f"`{i}` ({m})" for i, m in LEGACY) + "."]
    return md


def _gate_details(cells, verdicts) -> list[str]:
    md = ["", "## Gate details per cell (why each passed or failed)", ""]
    for key in sorted(cells, key=lambda k: (strategy_of(k[0]), k[0], k[1])):
        v = verdicts[key]
        md.append(f"- **{key[0]} / {key[1]}: {v['status']}**")
        for g, d in v["gates"].items():
            md.append(f"  - {g}: {'pass' if d['ok'] else 'FAIL' if d['ok'] is False else 'not run'} - {d['detail']}")
        sk = {}
        for r in cells[key]["assets"].values():
            for k, n in r["skipped"].items():
                sk[k] = sk.get(k, 0) + n
        md.append(f"  - setups {cells[key]['n_setups']}; skipped: {sk or 'none'}")
    return md


def _metrics_section(cells) -> list[str]:
    md = ["", "## Detailed metrics (pooled over assets, baseline bucket)", "",
          "| Variant | TF | n | Win % | Expectancy R | Avg win R | Avg loss R | Profit factor | Max DD (R) | Max consec. losses | "
          "Sharpe/trade | Sortino/trade | Avg hold (h) | Exposure | Fees/risk |", "|" + "---|" * 15]
    for key in sorted(cells, key=lambda k: (strategy_of(k[0]), k[0], k[1])):
        t = _pooled_all(cells[key])
        m = M.trade_metrics(t, window_bars=_window_bars(cells[key]))
        if not m["n"]:
            md.append(f"| {key[0]} | {key[1]} | 0 | - | - | - | - | - | - | - | - | - | - | - | - |")
            continue
        pf = m["profit_factor"]
        md.append(f"| {key[0]} | {key[1]} | {m['n']} | {m['win_rate']:.0%} | {fmt(m['expectancy_r'])} | "
                  f"{fmt(m['avg_win_r'])} | {fmt(m['avg_loss_r'])} | {'-' if pf is None else f'{pf:.2f}'} | "
                  f"{fmt(m['max_drawdown_r'], 2)} | {m['max_consecutive_losses']} | {fmt(m['sharpe_per_trade'], 2)} | "
                  f"{fmt(m['sortino_per_trade'], 2)} | {m['avg_hold_hours']:.1f} | "
                  f"{'-' if m['exposure'] is None else f'{m['exposure']:.1%}'} | {m['fees_share_of_risk']:.1%} |")
    md.append("")
    md.append(f"Sharpe/Sortino are per-trade ratios and shown only with >= {M.MIN_RATIO_TRADES} trades. Exposure = share of "
              "all bars in the window spent holding a position, summed over assets.")
    return md


def _window_bars(cell) -> int:
    # exposure denominator: 5m bars in the window summed over assets (approximation from first/last entry per asset)
    total = 0
    for r in cell["assets"].values():
        if r["trades"]:
            span = r["trades"][-1].exit_time - r["trades"][0].entry_time
            total += max(1, int(span.total_seconds() // 300))
    return total


def _regimes(cells) -> list[str]:
    md = ["", "## Performance by market regime (project regime label at entry; pooled)", "",
          "Regimes with fewer than 20 trades are shown but carry little weight.", "",
          "| Variant | TF | Regime | n | Expectancy R | Win % |", "|---|---|---|---|---|---|"]
    for key in sorted(cells, key=lambda k: (strategy_of(k[0]), k[0], k[1])):
        t = _pooled_all(cells[key])
        if len(t) < 20:
            continue
        for reg, m in M.regime_breakdown(t, lambda x: getattr(x, "regime", "UNKNOWN")).items():
            md.append(f"| {key[0]} | {key[1]} | {reg} | {m['n']} | {fmt(m['expectancy_r'])} | {m['win_rate']:.0%} |")
    return md


def _straddle(cells) -> list[str]:
    rows = []
    for key in sorted(cells):
        if variant_has_straddle(key[0]):
            e = M.straddle_economics(_pooled_all(cells[key]))
            if e:
                rows.append(f"| {key[0]} | {key[1]} | {e['avg_combined_premium_pct_of_spot']:.2f}% | "
                            f"{e['avg_breakeven_move_pct']:.2f}% | {e['avg_max_loss_usd_per_unit']:.2f} | "
                            f"{e['avg_fees_usd_per_unit']:.2f} |")
    if not rows:
        return ["", "## Straddle economics", "", "No straddle trades were produced."]
    return ["", "## Straddle economics (both legs priced, spread and fees on both legs)", "",
            "| Variant | TF | Avg combined premium (% of spot) | Avg break-even move (% of spot) | Avg max loss (USD per unit) | "
            "Avg fees (USD per unit) |", "|---|---|---|---|---|---|"] + rows + [
        "", "Break-even move = (combined premium + round-trip fees) / spot, ignoring exit spread; the strike is the spot proxy. "
        "A straddle needs the underlying to move about this much by exit to make money."]


def variant_has_straddle(vid: str) -> bool:
    try:
        return variant_by_id(vid).strategy_cls.policy == "LONG_STRADDLE"
    except KeyError:
        return False


def _cost_section(cells) -> list[str]:
    md = ["", "## Cost contribution (frictionless vs net)", "",
          "Frictionless = same signals with zero spread, no fees/GST and no breakeven gate (cells with stability runs only). "
          "Cost drag = frictionless minus net expectancy.", "",
          "| Variant | TF | Net R | Frictionless R | Cost drag (R) |", "|---|---|---|---|---|"]
    any_row = False
    for key in sorted(cells):
        fr = cells[key].get("frictionless")
        if not fr:
            continue
        s, n = E.pooled_sum_n(fr)
        t = _pooled_all(cells[key])
        net = E.mean_r(t)
        if n and net is not None:
            any_row = True
            md.append(f"| {key[0]} | {key[1]} | {fmt(net)} | {fmt(s / n)} | {fmt(s / n - net)} |")
    return md if any_row else md + ["No cell reached the stability stage."]


def _supertrend(cells, verdicts) -> list[str]:
    md = ["", "## Supertrend: previous result vs the standardised pipeline", "",
          "**Previous result** (quoted from `docs/research/STRATEGY_SEARCH.md`; discovery on BTC+ETH, holdout from "
          "2026-07-01; columns: trades, win %, net R, max DD in R):", "",
          "| Candidate | Discovery | Holdout | Holdout by asset net R (n) |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in OLD_SUPERTREND]
    md += ["", "**Same variants re-run through this pipeline** (all assets, all gates) plus the new filtered variants:", "",
           "| Variant | TF | Status | Trades | Pooled net R | Holdout net R (n) | Reasons |", "|---|---|---|---|---|---|---|"]
    for key in sorted(cells):
        if key[0].startswith("LEGACY") or key[0].startswith("S9"):
            t = _pooled_all(cells[key])
            _, hold = E.split_holdout(t)
            v = verdicts[key]
            md.append(f"| {key[0]} | {key[1]} | **{v['status']}** | {len(t)} | {fmt(E.mean_r(t))} | "
                      f"{fmt(E.mean_r(hold))} ({len(hold)}) | {'; '.join(v['reasons']) or '-'} |")
    md += ["", "The two legacy variants are NOT promoted by this report. Their status above is whatever the standard gates "
           "say."]
    return md


def _limitations(res) -> list[str]:
    return ["", "## Limitations (read before trusting any number)", "",
            "- **Option prices are modelled, not recorded.** There is no historical option-chain quote history yet (the "
            "chain recorder started on 2026-10-02). Prices are Black-Scholes at an as-of ATM IV inferred from real Delta "
            "option trades plus a fitted smile; spreads are a pessimistic calibration from one live snapshot (UNVERIFIED "
            "historically). Net R therefore carries model risk in both directions.",
            "- **XAUT** options were listed only around 2026-07-24, so XAUT has about two months of option history and is "
            "almost entirely inside the holdout. It rarely reaches the 30-trade per-asset minimum.",
            "- **Funding and OI** are hourly series. Where a window lacks them the funding/OI strategy returns NO SIGNAL "
            "(see dataset coverage above); nothing is filled in. Funding units are UNVERIFIED in the project notes.",
            "- **The selector reaches ATM and 1-ITM strikes only,** so no OTM bucket was tested.",
            "- **Opening-range sessions** for this 24x7 perp are documented assumptions (UTC), not exchange sessions.",
            "- **Multiple testing:** many cells were tested. A few positive cells are expected by chance, which is why "
            "ACCEPTED requires the random-signal control with a Bonferroni adjustment, and why EXPERIMENTAL is never "
            "'proven'.",
            "- **Sample size:** the window is about 10 months. A positive holdout of a few dozen trades is weak evidence "
            "whatever its sign.",
            "- GST (18%) and the fee schedule are the project's production values; the 18% GST is UNVERIFIED.",
            "- Nothing in this report changes the main project. No strategy here is registered, activated or tradeable."]


def to_json(res: dict) -> dict:
    out = {"protocol_hash": protocol_hash(), "since": res["since"], "timeframes": res["timeframes"],
           "errors": res["errors"], "summary": res["summary"], "info": res["info"], "cells": {}}
    for key, v in res["verdicts"].items():
        cell = res["cells"][key]
        t = _pooled_all(cell)
        disc, hold = E.split_holdout(t)
        out["cells"][f"{key[0]}|{key[1]}"] = {
            "strategy": strategy_of(key[0]), "status": v["status"], "reasons": v["reasons"], "gates": v["gates"],
            "n_trades": len(t), "n_setups": cell["n_setups"], "net_r": E.mean_r(t), "discovery_r": E.mean_r(disc),
            "holdout_r": E.mean_r(hold), "holdout_n": len(hold),
            "per_asset": {a: {"n": len(r["trades"]), "net_r": E.mean_r(r["trades"]), "skipped": r["skipped"]}
                          for a, r in cell["assets"].items()},
            "metrics": M.trade_metrics(t), "regimes": M.regime_breakdown(t, lambda x: getattr(x, "regime", "UNKNOWN")),
            "folds": M.fold_expectancies(t, E.N_FOLDS)}
    return json.loads(json.dumps(out, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o)))
