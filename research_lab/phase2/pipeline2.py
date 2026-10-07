"""Phase 2 orchestration. Order (cheap to expensive; later stages only for cells that can still change status):

  M. matrix: every applicable option-selection policy on every Phase-1 cell with >= 200 baseline trades (MODELLED prices).
     Workers spill each (cell, policy)'s trades to disk and return compact summaries only (memory-safe).
  S. selection: ONE policy per cell, chosen on DISCOVERY numbers only, then frozen (the holdout is never read to choose)
  C. stability (+-20% perturbations with the selected policy frozen) for cells with >= 200 trades and a positive holdout
  D. random-signal control for cells that passed every other gate
  R. real-data stage: REAL_ONLY / fallback pricing wherever recorded quotes overlap the signals; quality, coverage, and the
     model-vs-real quote comparison
Statuses come from the unchanged acceptance gates (`lab.evaluate`), a policy-breadth robustness gate, and the real-data bar.
Trades are loaded back from disk only for BASE and the selected policy of each cell.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from lab import evaluate as E
from lab.pipeline import PERP_FOR_ASSET, _cached, _run_parallel, strategy_of
from phase2 import analysis as A
from phase2 import runner2
from phase2.policies import POLICIES
from phase2.protocol2 import protocol2_hash

BUCKET_POLICIES = ("BASE", "DELTA-BROAD-030-060", "DELTA-ITM-050-070")  # the three delta buckets of the Phase-1 gate


def phase1_cells(path: Path, min_trades: int = E.MIN_TRADES) -> list[tuple[str, str]]:
    """Phase-1 cells with enough BASE trades to be worth the compute (the data-coverage rule)."""
    j = json.loads(path.read_text(encoding="utf-8"))
    return [tuple(k.split("|")) for k, c in j["cells"].items() if c["n_trades"] >= min_trades]  # type: ignore[misc]


def _assets_cell(per_asset: dict) -> dict:
    return {"assets": per_asset, "n_setups": sum(c["n_setups"] for c in per_asset.values())}


def _sum_n(trades) -> tuple[float, int]:
    return float(sum(t.net_r for t in trades)), len(trades)


def _sum_counters(cs) -> dict:
    out: dict = defaultdict(int)
    for c in cs:
        for k, n in c.items():
            out[k] += n
    return dict(out)


def _labels(trades) -> dict:
    out: dict = defaultdict(int)
    for t in trades:
        out[f"{t.entry_price_source}->{t.exit_price_source}"] += 1
    return dict(out)


def run(assets, timeframes, since, out_dir: Path, phase1_json: Path, reuse=False, workers=3,
        control_runs=E.CONTROL_RUNS, cells=None, policy_ids=None, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    h = f"{protocol2_hash()[:10]}_{since}_{'-'.join(timeframes)}_{'-'.join(assets)}"
    spill = str(out_dir / f"trades_{h}")
    perps = {a: PERP_FOR_ASSET[a] for a in assets}
    errors: dict = {}
    cells = cells or [c for c in phase1_cells(phase1_json) if c[1] in timeframes]
    pids = policy_ids or [p.id for p in POLICIES]
    n_per = defaultdict(int)
    for vid, tf in cells:
        n_per[strategy_of(vid)] += 1

    def split(pair):
        errors.update(pair[1])
        return pair[0]

    log(f"M: {len(cells)} cells x {len(pids)} policies on modelled prices (trades spill to disk) ...")
    m = _cached(out_dir / f"p2_matrix_{h}.pkl", reuse, lambda: split(_run_parallel(
        runner2.matrix, {a: (p, since, timeframes, cells, pids, spill) for a, p in perps.items()}, workers, log)), log)
    info = {a: r["info"] for a, r in m.items()}
    summ: dict = defaultdict(lambda: defaultdict(dict))  # (vid, tf) -> pid -> asset -> summary
    for a, r in m.items():
        for (vid, tf, pid), c in r["cells"].items():
            summ[(vid, tf)][pid][a] = c
    log(f"  done ({time.time() - t0:.0f}s)")

    # ---- S: discovery-only selection (from summaries) --------------------------------------------------------------------------
    results: dict = {}
    for key, by_pid in summ.items():
        order = [p for p in pids if p in by_pid]
        pooled = {pid: {per: A.pool_metrics([c["periods"][per] for c in by_a.values()]) for per in ("all", "discovery", "holdout")}
                  for pid, by_a in by_pid.items()}
        disc = {pid: (v["discovery"].get("n", 0), v["discovery"].get("net_r")) for pid, v in pooled.items()}
        hold = {pid: (v["holdout"].get("n", 0), v["holdout"].get("net_r")) for pid, v in pooled.items()}
        results[key] = {"by_pid": by_pid, "pooled": pooled, "order": order, "selection": A.select_from_sums(disc, order),
                        "hold": hold}

    def trades_of(key, pid) -> dict:
        vid, tf = key
        out = {}
        for a in results[key]["by_pid"][pid]:
            c = results[key]["by_pid"][pid][a]
            out[a] = {"trades": runner2.load_spilled(spill, a, vid, tf, pid), "n_setups": c["n_setups"], "skipped": c["skipped"]}
        return out

    sel_cells: dict = {}
    for key, r in results.items():
        pid = r["selection"]["selected"]
        cell = _assets_cell(trades_of(key, pid))
        cell["buckets"] = {}
        for bp in BUCKET_POLICIES:
            if bp in r["by_pid"]:
                cell["buckets"][f"{bp}@2.5"] = {"holdout": {a: tuple(c["hold_sum_n"]) for a, c in r["by_pid"][bp].items()}, "all": {}}
        sel_cells[key] = cell
    cand = [(v, t, results[(v, t)]["selection"]["selected"]) for (v, t), c in sel_cells.items()
            if E.needs_stability_runs({**c, "perturb": None, "buckets": None})]
    log(f"C: stability runs for {len(cand)} of {len(sel_cells)} cells ...")
    if cand:
        c_res = _cached(out_dir / f"p2_stability_{h}.pkl", reuse, lambda: split(_run_parallel(
            runner2.stability, {a: (p, since, timeframes, cand) for a, p in perps.items()}, workers, log)), log)
        for a, r in c_res.items():
            for (vid, tf, pid), per_lab in r["cells"].items():
                pp = sel_cells[(vid, tf)].setdefault("perturb", {})
                for lab, v in per_lab.items():
                    if v is None:
                        pp[lab] = None
                    elif pp.get(lab, {}) is not None:
                        pp.setdefault(lab, {})[a] = v
    ctl = [(v, t, results[(v, t)]["selection"]["selected"]) for (v, t), c in sel_cells.items()
           if "perturb" in c and E.needs_control(c, n_per[strategy_of(v)])]
    log(f"D: random-signal control ({control_runs} runs) for {len(ctl)} cells ...")
    if ctl:
        d_res = _cached(out_dir / f"p2_control_{h}.pkl", reuse, lambda: split(_run_parallel(
            runner2.control, {a: (p, since, timeframes, ctl, control_runs) for a, p in perps.items()}, workers, log)), log)
        for a, r in d_res.items():
            for (vid, tf, pid), series in r["cells"].items():
                sel_cells[(vid, tf)].setdefault("control", {})[a] = series

    # ---- R: real-data stage -----------------------------------------------------------------------------------------------------
    jobs = sorted({(v, t, "BASE") for v, t in results} | {(v, t, r["selection"]["selected"]) for (v, t), r in results.items()})
    log(f"R: real-data stage for {len(jobs)} configurations ...")
    r_res = split(_run_parallel(runner2.real_stage, {a: (p, since, timeframes, jobs) for a, p in perps.items()},
                                workers, log)) if jobs else {}
    snapshot_days = max([(x["quality"].get("snapshot_days") or 0) for x in r_res.values()] or [0])

    # ---- status -------------------------------------------------------------------------------------------------------------------
    out_cells: dict = {}
    all_bucket_parts: list = []
    for key, r in results.items():
        vid, tf = key
        pid = r["selection"]["selected"]
        breadth, n_eval = A.breadth_from_sums(r["hold"], list(r["order"]))
        real_cell, real_notes = None, {}
        for a, rr in r_res.items():
            rc = rr["cells"].get((vid, tf, pid))
            if rc is not None:
                real_notes[a] = {"signals_in_window": rc["n_signals_in_window"], "signals_total": rc["n_signals_total"],
                                 "real_only_trades": len(rc["REAL_ONLY"]["trades"]), "real_only_skipped": rc["REAL_ONLY"]["skipped"],
                                 "fallback_trades": len(rc["REAL_THEN_MODEL_FALLBACK"]["trades"]),
                                 "fallback_skipped": rc["REAL_THEN_MODEL_FALLBACK"]["skipped"],
                                 "fallback_labels": _labels(rc["REAL_THEN_MODEL_FALLBACK"]["trades"]),
                                 "unpriced_exits": len(rc["REAL_ONLY"]["unpriced_exits"])}
        have_real = [a for a, n in real_notes.items() if n["real_only_trades"] > 0]
        if have_real:
            real_cell = _assets_cell({a: {"trades": r_res[a]["cells"][(vid, tf, pid)]["REAL_ONLY"]["trades"],
                                          "n_setups": r_res[a]["cells"][(vid, tf, pid)]["n_signals_in_window"],
                                          "skipped": r_res[a]["cells"][(vid, tf, pid)]["REAL_ONLY"]["skipped"]} for a in have_real})
        st = A.final_status(sel_cells[key], n_per[strategy_of(vid)], breadth, real_cell, snapshot_days=snapshot_days)
        base_trades = sorted((t for c in trades_of(key, "BASE").values() for t in c["trades"]), key=lambda t: t.entry_time)
        sel_trades = sorted((t for c in sel_cells[key]["assets"].values() for t in c["trades"]), key=lambda t: t.entry_time)
        parts = [c["buckets"] for by_a in r["by_pid"].values() for c in by_a.values()]
        all_bucket_parts.append(A.merge_bucket_sums(parts))
        out_cells[key] = {
            "selected": pid, "selection": r["selection"], "status": st["status"], "reasons": st["reasons"], "gates": st["gates"],
            "real_data": st["real_data"], "breadth": breadth, "n_evaluable_policies": n_eval, "real_notes": real_notes,
            "signal_vs_option": {"BASE": A.signal_vs_option(base_trades), "selected": A.signal_vs_option(sel_trades)},
            "attribution": {"BASE": A.attribution(base_trades), "selected": A.attribution(sel_trades)},
            "buckets": {k: v.to_dict("records") for k, v in A.bucket_frames(
                A.bucket_sums(sel_trades)).items()},
            "policies": {pid2: {**r["pooled"][pid2], "per_asset": {a: c["periods"]["all"] for a, c in r["by_pid"][pid2].items()},
                                "skipped": _sum_counters(c["skipped"] for c in r["by_pid"][pid2].values())}
                         for pid2 in r["order"]}}
        del base_trades, sel_trades
    reproduced = _reproduces_phase1(results, phase1_json)
    mvr = {a: x["model_vs_real"] for a, x in r_res.items() if x.get("model_vs_real")}
    return {"cells": out_cells, "info": info, "errors": errors, "since": since, "timeframes": list(timeframes),
            "real": {"quality": {a: x["quality"] for a, x in r_res.items()}, "coverage": {a: x["coverage"] for a, x in r_res.items()},
                     "model_vs_real": mvr, "snapshot_days": snapshot_days},
            "phase1_reproduced": reproduced, "n_cells_per_strategy": dict(n_per), "control_runs": control_runs,
            "policies": [p.id for p in POLICIES], "seconds": time.time() - t0,
            "agg": {"bucket_sums": A.merge_bucket_sums(all_bucket_parts)}}


def _reproduces_phase1(results: dict, phase1_json: Path) -> dict:
    """BASE through the Phase-2 code path must equal the Phase-1 numbers (MODEL_ONLY reproducibility)."""
    j = json.loads(phase1_json.read_text(encoding="utf-8"))
    bad, ok = [], 0
    for (vid, tf), r in results.items():
        ref = j["cells"].get(f"{vid}|{tf}")
        base = r["pooled"].get("BASE", {}).get("all")
        if ref is None or not base:
            continue
        same = base.get("n", 0) == ref["n_trades"] and (not base.get("n") or abs(base["net_r"] - ref["net_r"]) < 1e-9)
        ok += same
        if not same:
            bad.append(f"{vid}|{tf}: now {base.get('n', 0)} trades, phase 1 {ref['n_trades']}")
    return {"identical_cells": ok, "different_cells": bad}
