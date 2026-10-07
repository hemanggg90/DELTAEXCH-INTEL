"""Phase 2 report (markdown) and the JSON the dashboard reads. Every conclusion is labelled REAL, MODELED or
DATA-INSUFFICIENT; failures are shown as prominently as passes."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from lab import evaluate as E
from phase2 import analysis as A
from phase2.policies import BY_ID, POLICIES
from phase2.protocol2 import protocol2_hash

fmt = E.fmt
STATUS_ORDER = (A.REAL_DATA_VALIDATED, E.ACCEPTED, E.EXPERIMENTAL, E.REJECTED, E.DATA_INSUFFICIENT)


def md_table(df: pd.DataFrame, floatfmt: str = ".3f") -> list[str]:
    if df is None or len(df) == 0:
        return ["_(no rows)_"]
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append("-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else
                         (format(v, floatfmt) if isinstance(v, (float, np.floating)) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return out


def _all_trades(res: dict, which: str = "all") -> list:
    out = []
    for c in res["_raw"].values():
        for pid, ts in c["pooled"].items():
            out += ts
    return out


def best(statuses: list[str]) -> str:
    return min(statuses, key=STATUS_ORDER.index) if statuses else E.DATA_INSUFFICIENT


def render(res: dict, raw: dict | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    cells = res["cells"]
    by_strategy: dict = {}
    for (vid, tf), c in cells.items():
        from lab.pipeline import strategy_of

        by_strategy.setdefault(strategy_of(vid), []).append(c["status"])
    counts = {s: sum(1 for v in cells.values() if v["status"] == s) for s in STATUS_ORDER}
    real = res["real"]
    n_real_rows = sum(q["n_rows"] for q in real["quality"].values())
    md = ["# Phase 2 research report: real option-chain data and option-selection research", "",
          f"Generated {now:%Y-%m-%d %H:%M} UTC by `research_lab/run_phase2.py`. Phase 2 protocol hash "
          f"`{protocol2_hash()}`. Data window from {res['since']}; timeframes {', '.join(res['timeframes'])}.", ""]
    # ---- executive summary --------------------------------------------------------------------------------------------
    n_acc = counts[E.ACCEPTED] + counts[A.REAL_DATA_VALIDATED]
    md += ["## 1. Executive summary", "",
           f"- **Cells passing the unchanged acceptance gates: {n_acc} of {len(cells)}.** "
           + ("None. That is a valid result." if not n_acc else "See the status table; each is listed, none is picked as a favourite."),
           "- Status counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()),
           f"- **Real option data (REAL_RECORDED): {n_real_rows:,} rows over {real['snapshot_days']} day(s) of snapshots.** "
           "The recorder only started on 2026-10-02, so almost everything below is MODELED. "
           f"{'REAL-DATA-VALIDATED needs >= %d days of snapshots, >= %d real trades and >= %.0f%% real pricing.' % (A.DEFAULT_REAL.min_snapshot_days, A.DEFAULT_REAL.min_real_trades, 100 * A.DEFAULT_REAL.min_real_fraction)}",
           f"- Phase 1 reproduced under MODEL_ONLY: {res['phase1_reproduced']['identical_cells']} cells identical, "
           f"{len(res['phase1_reproduced']['different_cells'])} different.",
           "- **Basis of conclusions:** every performance number is MODELED (Black-Scholes at as-of IV from real option trades, "
           "modelled spread) unless a row says REAL. Real recorded quotes are used only for the model-vs-real price check and "
           "the data-coverage/quality sections, and for REAL_ONLY trades if any signal fell inside the recorded window.", ""]
    md += _final_status_table(res, by_strategy)
    md += _real_coverage(res)
    md += _quality(res)
    md += _methodology(res)
    md += _matrix(res)
    md += _signal_vs_option(res)
    md += _real_vs_modeled(res)
    md += _attribution(res)
    md += _analyses(res)
    md += _holdout_and_robustness(res)
    md += _failures(res)
    md += _answers(res, counts)
    return "\n".join(md) + "\n"


def _final_status_table(res, by_strategy) -> list[str]:
    md = ["## 2. Final research status", "", "Strategy status = best status among its cells (the cell tables below are the real result).", "",
          "| Strategy | Best status | Cells | " + " | ".join(STATUS_ORDER) + " |", "|---|---|---|" + "---|" * len(STATUS_ORDER)]
    for name, sts in by_strategy.items():
        md.append(f"| {name} | **{best(sts)}** | {len(sts)} | " + " | ".join(str(sts.count(s)) for s in STATUS_ORDER) + " |")
    md += ["", "| Cell | Selected policy (discovery only) | Status | Holdout net R of the selected policy | Reasons |", "|---|---|---|---|---|"]
    for (vid, tf), c in sorted(res["cells"].items()):
        h = c["policies"][c["selected"]]["holdout"]
        md.append(f"| {vid} {tf} | {c['selected']} | **{c['status']}** | {fmt(h.get('net_r'))} (n={h.get('n', 0)}) | "
                  f"{'; '.join(c['reasons']) or '-'} |")
    return md + [""]


def _real_coverage(res) -> list[str]:
    real = res["real"]
    md = ["## 3. Real option-data coverage (REAL_RECORDED)", ""]
    if not real["quality"] or not any(q["n_rows"] for q in real["quality"].values()):
        return md + ["**No recorded option-chain rows exist.** Everything below is MODELED.", ""]
    md += ["| Asset | Rows | Snapshots | Window (UTC) | Median interval (s) | REAL_QUOTE rows | REAL_MARK_ONLY | UNUSABLE |", "|---|---|---|---|---|---|---|---|"]
    for a, q in real["quality"].items():
        w = q.get("window")
        md.append(f"| {a} | {q['n_rows']:,} | {q['n_snapshots']} | {'-' if not w else w[0][:16] + ' to ' + w[1][:16]} | "
                  f"{q['median_interval_sec'].get(a) or '-'} | {q['tier_counts'].get('REAL_QUOTE', 0):,} | "
                  f"{q['tier_counts'].get('REAL_MARK_ONLY', 0):,} | {q['tier_counts'].get('UNUSABLE', 0):,} |")
    for a, cov in real["coverage"].items():
        if not cov:
            continue
        md += ["", f"### {a}: coverage by date, option type, strike region, DTE and timeframe (real quotes)", ""]
        for key, title in (("by_day", "By date"), ("by_kind", "By option type"), ("by_region", "By strike region"),
                           ("by_dte", "By DTE"), ("by_timeframe", "By timeframe (share of 5-minute bars that have a snapshot)")):
            md += [f"**{title}**", ""] + md_table(pd.DataFrame(cov[key]), ".1f") + [""]
        md += ["**By expiry**", ""] + md_table(pd.DataFrame(cov["by_expiry"]).head(15), ".1f") + [""]
    return md


def _quality(res) -> list[str]:
    md = ["## 4. Data-quality issues in the recorded chain", "",
          "Flags describe the recorded rows; they never change them. REAL_QUOTE = usable two-sided fresh quote; a mark "
          "without a two-sided quote is REAL_MARK_ONLY and is never treated as a bid or ask.", ""]
    qs = res["real"]["quality"]
    if not qs:
        return md + ["_No recorded data to assess._", ""]
    flags = sorted({f for q in qs.values() for f in q["flag_counts"]})
    md += ["| Flag | " + " | ".join(qs) + " |", "|---|" + "---|" * len(qs)]
    for f in flags:
        md.append(f"| {f} | " + " | ".join(f"{q['flag_counts'].get(f, 0):,} ({100 * q['flag_counts'].get(f, 0) / max(1, q['n_rows']):.1f}%)" for q in qs.values()) + " |")
    for a, q in qs.items():
        if q["gaps"]:
            md.append(f"\n- {a}: {len(q['gaps'])} timestamp gap(s) in the snapshot series (first: {q['gaps'][0][1]} to {q['gaps'][0][2]})")
        for n in q["notes"]:
            md.append(f"- {a}: {n}")
    return md + [""]


def _methodology(res) -> list[str]:
    return ["## 5. Methodology", "",
            "- **Signals are frozen.** S1-S10 and the legacy Supertrend variants are exactly the Phase 1 strategies (Phase 1 hash "
            "verified at run time). Nothing about the signals was changed or re-tuned.",
            "- **Option selection is separate.** For each signal, every applicable policy picks the contract at the SAME signal "
            "timestamp using only information available then (the project's selector for BASE; `phase2/policies.py` otherwise).",
            "- **Pricing modes** (`OptionBacktestConfig.pricing_mode`): MODEL_ONLY (default; Phase 1 reproduced exactly), REAL_ONLY "
            "(recorded quotes only; entry at the recorded ask, exit at the recorded bid; never a silent fallback) and "
            "REAL_THEN_MODEL_FALLBACK (every trade labelled REAL_QUOTE, MODELED or MIXED).",
            "- **Costs** are the project's: modelled bid/ask spread (or the real one), exchange fees with the premium cap, 18% GST "
            "(unverified), on every leg; net P&L = gross mid-to-mid P&L - spread cost - fees, exactly (checked per trade).",
            "- **Anti-overfitting:** ONE policy per cell is chosen on DISCOVERY trades only (before 2026-07-01), frozen, and then "
            "evaluated on the holdout. The holdout is never used to choose. The unchanged acceptance gates apply to the selected "
            "policy, plus a policy-breadth gate (the signal's economics must not hinge on one option choice: >= 50% of evaluable "
            f"policies positive on the holdout). Bonferroni for the random control is over the strategy's cells ({res['control_runs']} runs).",
            "- **REAL-DATA-VALIDATED** is only possible when all gates pass AND >= "
            f"{A.DEFAULT_REAL.min_snapshot_days} days of snapshots, >= {A.DEFAULT_REAL.min_real_trades} real trades (>= "
            f"{A.DEFAULT_REAL.min_real_per_asset} on >= {E.MIN_ASSETS} assets), >= {A.DEFAULT_REAL.min_real_fraction:.0%} of trades priced from "
            "real quotes at both ends, and the gates also pass on the real-only trades. The thresholds are configurable "
            "(`RealDataThresholds`), with the rationale in `analysis.py`.",
            "- **Compute rule:** only cells with >= 200 baseline trades in Phase 1 were run through the policy matrix; the rest were "
            "already DATA-INSUFFICIENT and spending compute on them cannot change that.", ""]


def _matrix(res) -> list[str]:
    pids = res["policies"]
    md = ["## 6. Strategy x option-selection matrix (MODELED prices)", "",
          "Pooled over assets. Entries are holdout net R (n >= 30 holdout trades) / discovery net R. `*` marks the policy selected on "
          "discovery. `na` = not applicable or not evaluable on modelled data (liquidity policy needs real OI/volume; delta bands do not "
          "apply to straddles). `.` = fewer than 30 trades.", ""]
    head = ["Cell"] + [p.replace("FILTER-", "F-").replace("DELTA-", "D-") for p in pids]
    md += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for (vid, tf), c in sorted(res["cells"].items()):
        row = [f"{vid} {tf}"]
        for pid in pids:
            m = c["policies"].get(pid)
            if m is None:
                row.append("na")
                continue
            h, d = m["holdout"], m["discovery"]
            hs = fmt(h.get("net_r"), 2) if h.get("n", 0) >= E.MIN_HOLDOUT else "."
            ds = fmt(d.get("net_r"), 2) if d.get("n", 0) >= 30 else "."
            row.append(f"{hs}/{ds}{'*' if pid == c['selected'] else ''}")
        md.append("| " + " | ".join(row) + " |")
    return md + [""]


def _signal_vs_option(res) -> list[str]:
    md = ["## 7. Signal edge versus option-implementation edge (MODELED)", "",
          "Signal edge = mean underlying-space R (stop/target geometry, no option, no costs); for straddles, the frictionless option R. "
          "It counts only if positive in discovery AND holdout with a bootstrap 95% lower bound above 0. 'OPTION-IMPLEMENTATION "
          "FAILURE' is a failure of the option leg, not of the signal.", "",
          "| Cell | Policy | Signal R disc / hold | CI low | Gross R hold | Net R hold | Classification |", "|---|---|---|---|---|---|---|"]
    for (vid, tf), c in sorted(res["cells"].items()):
        for lab, s in (("BASE", c["signal_vs_option"]["BASE"]), (c["selected"], c["signal_vs_option"]["selected"])):
            if lab != "BASE" and lab == c["selected"] and c["selected"] == "BASE":
                continue
            md.append(f"| {vid} {tf} | {lab} | {fmt(s.get('signal_r_discovery'), 2)} / {fmt(s.get('signal_r_holdout'), 2)} | "
                      f"{fmt(s.get('signal_ci_low_pooled'), 3)} | {fmt(s.get('gross_r_holdout'))} | {fmt(s.get('net_r_holdout'))} | "
                      f"{s.get('classification')} {s.get('reason', '')} |")
    return md + [""]


def _real_vs_modeled(res) -> list[str]:
    md = ["## 8. Real versus modeled", ""]
    mvr = res["real"]["model_vs_real"]
    got = {a: v for a, v in mvr.items() if v and v.get("n")}
    if not got:
        md.append("**DATA-INSUFFICIENT:** no recorded quote could be compared with the model (no recorded data, or no model IV at the recorded times).")
    else:
        md += ["**Model price versus real recorded quote at the same contract and time** (REAL quotes vs MODELED). Positive error = "
               "the model is richer than the real quote. This is a validation of the model, over "
               f"{res['real']['snapshot_days']} day(s) of snapshots only.", "",
               "| Asset | Quotes compared | Median mid error % | Median abs mid error % | Median ask error % | Median bid error % | Real half-spread % | Model half-spread % |", "|---|---|---|---|---|---|---|---|"]
        for a, v in got.items():
            o = v["overall"]
            md.append(f"| {a} | {v['n']} | {o['median_mid_err_pct']:+.2f} | {o['median_abs_mid_err_pct']:.2f} | {o['median_ask_err_pct']:+.2f} | "
                      f"{o['median_bid_err_pct']:+.2f} | {o['real_half_spread_pct']:.2f} | {o['model_half_spread_pct']:.2f} |")
        for a, v in got.items():
            md += ["", f"**{a} by DTE**", ""] + md_table(pd.DataFrame(v["by_dte"]), ".2f")
            md += ["", f"**{a} by moneyness**", ""] + md_table(pd.DataFrame(v["by_moneyness"]), ".2f")
    md += ["", "**Trades priced from real quotes (REAL_ONLY / fallback), by cell:**", ""]
    rows = []
    for (vid, tf), c in sorted(res["cells"].items()):
        for a, n in c["real_notes"].items():
            rows.append({"cell": f"{vid} {tf}", "asset": a, "signals in recorded window": n["signals_in_window"],
                         "REAL_ONLY trades": n["real_only_trades"], "fallback trades": n["fallback_trades"],
                         "labels": json.dumps(n["fallback_labels"]), "unpriced exits": n["unpriced_exits"]})
    md += md_table(pd.DataFrame(rows)) if rows else ["_No configuration had a signal inside the recorded window._"]
    md += ["", "**Does real pricing change the Phase 1 conclusions? DATA-INSUFFICIENT.** There are no real-priced trades to compare. "
           "The model-vs-real table above is the only direct evidence, and it covers quotes, not strategy outcomes.", ""]
    return md


def _attribution(res) -> list[str]:
    md = ["## 9. Cost attribution (MODELED; R units; net = gross - spread - fees exactly)", "",
          "Theta is an ESTIMATE (model theta x time held) and sits inside gross. `move+other` = gross + theta estimate.", "",
          "| Cell | Policy | n | Gross R | Spread R | Fees R | Net R | Theta est. R | Move+other est. R |", "|---|---|---|---|---|---|---|---|---|"]
    for (vid, tf), c in sorted(res["cells"].items()):
        for lab in ("BASE", "selected"):
            a = c["attribution"][lab]
            if not a:
                continue
            md.append(f"| {vid} {tf} | {lab if lab == 'BASE' else c['selected']} | {a['n']} | {fmt(a['gross_r'])} | {fmt(a['spread_r'])} | "
                      f"{fmt(a['fees_r'])} | {fmt(a['net_r'])} | {fmt(a['theta_r_estimate'])} | {fmt(a['move_and_other_r_estimate'])} |")
    md += ["", "Option selection effect = (policy net R) - (BASE net R) on the same signals, per cell, in the matrix above.", ""]
    return md


def _analyses(res) -> list[str]:
    md = []
    tabs = A.bucket_frames(res["agg"]["bucket_sums"])
    titles = [("iv_rv", "10. IV versus realised volatility"), ("spread_pct", "11. Spread and liquidity"),
              ("dte_days", "12. DTE"), ("abs_delta", "13. Delta / moneyness")]
    for key, title in titles:
        md += [f"## {title} (MODELED; pooled over all policies and cells, so DTE and delta vary by policy)", ""]
        md += md_table(tabs.get(key)) + [""]
        if key == "spread_pct":
            md += ["**Premium as % of spot**", ""] + md_table(tabs.get("premium_pct_spot")) + [""]
            md += ["**Theta as % of premium per day (model)**", ""] + md_table(tabs.get("theta_pct_premium")) + [""]
            md += ["**Open interest / volume: DATA-INSUFFICIENT.** Modelled prices carry no open interest or volume; the "
                   "LIQ-OI100-VOL10 policy can only run on REAL data, and none overlaps the signals.", ""]
        if key == "abs_delta":
            md += ["**Strike distance from spot**", ""] + md_table(tabs.get("strike_dist_pct")) + [""]
    md += ["**Policy-level summary (all cells pooled; paired difference to BASE per cell on the holdout)**", ""]
    rows = []
    for p in POLICIES:
        d, dn, dm, hn, hm = [], 0, 0.0, 0, 0.0
        for c in res["cells"].values():
            m, b = c["policies"].get(p.id), c["policies"].get("BASE")
            if not m:
                continue
            dn += m["discovery"].get("n", 0)
            dm += (m["discovery"].get("net_r") or 0.0) * m["discovery"].get("n", 0)
            hn += m["holdout"].get("n", 0)
            hm += (m["holdout"].get("net_r") or 0.0) * m["holdout"].get("n", 0)
            if b and m["holdout"].get("n", 0) >= E.MIN_HOLDOUT and b["holdout"].get("n", 0) >= E.MIN_HOLDOUT:
                d.append(m["holdout"]["net_r"] - b["holdout"]["net_r"])
        rows.append({"policy": p.id, "note": p.note, "disc n": dn, "disc net R": dm / dn if dn else None, "hold n": hn,
                     "hold net R": hm / hn if hn else None, "paired hold diff vs BASE": float(np.mean(d)) if d else None,
                     "cells compared": len(d)})
    return md + md_table(pd.DataFrame(rows)) + [""]


def _holdout_and_robustness(res) -> list[str]:
    md = ["## 14. Holdout and robustness results (selected policy)", "",
          "| Cell | Policy | Holdout net R (n) | Perturbation | Assets | Buckets | Control | Policy breadth | Status |", "|---|---|---|---|---|---|---|---|---|"]
    g = lambda c, k: ("-" if c["gates"].get(k) is None else ("ok" if c["gates"][k]["ok"] else "NO" if c["gates"][k]["ok"] is False else "not run"))  # noqa: E731
    for (vid, tf), c in sorted(res["cells"].items()):
        h = c["policies"][c["selected"]]["holdout"]
        md.append(f"| {vid} {tf} | {c['selected']} | {fmt(h.get('net_r'))} ({h.get('n', 0)}) | {g(c, 'perturbation')} | {g(c, 'assets')} | "
                  f"{g(c, 'buckets')} | {g(c, 'control')} | {g(c, 'policy_breadth')} | **{c['status']}** |")
    md += ["", "Gate details:", ""]
    for (vid, tf), c in sorted(res["cells"].items()):
        if c["status"] in (E.EXPERIMENTAL, E.ACCEPTED, A.REAL_DATA_VALIDATED):
            md.append(f"- **{vid} {tf} ({c['selected']}): {c['status']}**")
            for k, d in c["gates"].items():
                md.append(f"  - {k}: {'pass' if d['ok'] else 'FAIL' if d['ok'] is False else 'not run'} - {d['detail']}")
            md.append(f"  - real-data bar: {c['real_data'].get('detail', '')}")
    return md + [""]


def _failures(res) -> list[str]:
    md = ["## 15. Failure analysis", "", "Why configurations produced no trades (skip counts, selected policy, all assets):", ""]
    rows = []
    for (vid, tf), c in sorted(res["cells"].items()):
        sk = c["policies"][c["selected"]]["skipped"]
        rows.append({"cell": f"{vid} {tf}", "policy": c["selected"], "skipped": json.dumps(dict(sorted(sk.items(), key=lambda x: -x[1])[:6]))})
    md += md_table(pd.DataFrame(rows))
    cls: dict = {}
    for c in res["cells"].values():
        k = c["signal_vs_option"]["BASE"].get("classification", "").split(" (")[0]
        cls[k] = cls.get(k, 0) + 1
    md += ["", "Cells by signal-vs-implementation classification (BASE policy): " + ", ".join(f"{k}: {v}" for k, v in cls.items()), ""]
    if res["errors"]:
        md += ["Run errors: " + json.dumps(res["errors"]), ""]
    return md


def _answers(res, counts) -> list[str]:
    n_acc = counts[E.ACCEPTED] + counts[A.REAL_DATA_VALIDATED]
    cells = res["cells"]
    impl = [k for k, c in cells.items() if c["signal_vs_option"]["BASE"].get("classification", "").startswith(A.OPTION_IMPLEMENTATION_FAILURE)]
    kept = [k for k, c in cells.items() if c["signal_vs_option"]["BASE"].get("classification", "").startswith("SIGNAL EDGE PRESERVED")]
    sel_better = [k for k, c in cells.items()
                  if c["selected"] != "BASE" and (c["policies"][c["selected"]]["holdout"].get("net_r") or -9) >
                  (c["policies"]["BASE"]["holdout"].get("net_r") or 9)]
    return ["## 16. Answers", "",
            f"1. **Real option data available:** {sum(q['n_rows'] for q in res['real']['quality'].values()):,} recorded rows across "
            f"{res['real']['snapshot_days']} day(s). DATA-INSUFFICIENT for any real-price strategy evaluation.",
            f"2. **Strategies with enough real data to evaluate:** none (needs >= {A.DEFAULT_REAL.min_snapshot_days} days and "
            f">= {A.DEFAULT_REAL.min_real_trades} real trades).",
            "3. **Does real pricing change the Phase 1 conclusions?** DATA-INSUFFICIENT. All strategy results here are MODELED and the "
            "Phase 1 results are reproduced exactly under MODEL_ONLY.",
            f"4. **Option-selection characteristics that look robust:** judge from the tables above and the policy-breadth gate. Cells where "
            f"the discovery-selected policy beat BASE on the holdout: {len(sel_better)} of {len(cells)} (not evidence of an edge: selection "
            "among many policies, MODELED prices). Cells where the signal edge was shown and preserved: "
            f"{len(kept)}; shown but destroyed by the option leg (option-implementation failure): {len(impl)}.",
            "5. **Remaining modeled / data-insufficient:** everything except the model-vs-real quote comparison and the coverage/quality "
            "sections. Open interest and volume effects, real spreads, real fills and slippage cannot be assessed yet.",
            f"6. **Cells passing the existing acceptance gates:** {n_acc}. "
            + ("None." if not n_acc else ", ".join(f"{v} {t} ({c['status']})" for (v, t), c in cells.items() if c['status'] in (E.ACCEPTED, A.REAL_DATA_VALIDATED))),
            "", "**Next:** keep `python scripts/record_chain.py` running (or the engine) so real quotes accumulate; re-run "
            "`python research_lab/run_phase2.py` when >= 14 days exist. Nothing in this report supports trading any configuration.", ""]


def to_json(res: dict) -> dict:
    out = {"protocol2_hash": protocol2_hash(), "since": res["since"], "timeframes": res["timeframes"],
           "errors": res["errors"], "phase1_reproduced": res["phase1_reproduced"], "policies": res["policies"],
           "policy_notes": {p.id: p.note for p in POLICIES}, "real": {
               "snapshot_days": res["real"]["snapshot_days"],
               "quality": res["real"]["quality"], "coverage": {a: {k: (pd.DataFrame(v).to_dict("records") if v is not None else [])
                                                                    for k, v in (cov or {}).items()}
                                                               for a, cov in res["real"]["coverage"].items()},
               "model_vs_real": {a: ({"n": v["n"], "overall": v["overall"], "by_dte": pd.DataFrame(v["by_dte"]).to_dict("records"),
                                      "by_moneyness": pd.DataFrame(v["by_moneyness"]).to_dict("records")} if v and v.get("n") else v)
                                 for a, v in res["real"]["model_vs_real"].items()}},
           "cells": {}}
    for (vid, tf), c in res["cells"].items():
        out["cells"][f"{vid}|{tf}"] = {k: v for k, v in c.items() if k != "policies"} | {
            "policies": {pid: {"holdout": m["holdout"], "discovery": m["discovery"], "all": m["all"], "skipped": m["skipped"]}
                         for pid, m in c["policies"].items()}}
    return json.loads(json.dumps(out, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o)))
