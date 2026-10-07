"""Orchestration: staged runs, merging per-asset worker output into cells, classification.

Stages (cheap to expensive; a later stage runs only for cells whose earlier results can still change the verdict):
  A. baseline backtest of every variant x timeframe x asset;
  C. stability runs (+-20% perturbations, option buckets, DTE, frictionless) for cells with >= 200 trades and a
     positive holdout;
  D. random-signal control (300 runs) for cells that passed every other gate.
Every cell's status is computed from whatever was run; a gate that did not run is shown as "not run", never assumed.
"""
from __future__ import annotations

import pickle
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lab import evaluate as E
from lab import runner
from lab.library import VARIANTS, strategies_in_order
from lab.protocol import protocol_hash

LEGACY_GROUP = "Supertrend (project's active variants, legacy)"
PERP_FOR_ASSET = {"BTC": "BTCUSD", "ETH": "ETHUSD", "XAUT": "XAUTUSD"}


def strategy_of(variant_id: str) -> str:
    if variant_id.startswith("LEGACY"):
        return LEGACY_GROUP
    return next(v.strategy_name for v in VARIANTS if v.id == variant_id)


def _run_parallel(fn: Callable, argsets: dict[str, tuple], workers: int, log: Callable[[str], None]) -> tuple[dict, dict]:
    """Run fn(*args) per asset in a process pool; one failing asset does not stop the others."""
    results, errors = {}, {}
    if workers <= 1 or len(argsets) == 1:
        for a, args in argsets.items():
            try:
                results[a] = fn(*args)
            except Exception as exc:  # reported, never hidden
                errors[a] = f"{type(exc).__name__}: {exc}"
                log(f"  {a}: FAILED {errors[a]}")
        return results, errors
    with ProcessPoolExecutor(max_workers=min(workers, len(argsets))) as ex:
        futs = {a: ex.submit(fn, *args) for a, args in argsets.items()}
        for a, fu in futs.items():
            try:
                results[a] = fu.result()
            except Exception as exc:
                errors[a] = f"{type(exc).__name__}: {exc}"
                log(f"  {a}: FAILED {errors[a]}")
    return results, errors


def _cached(path: Path, reuse: bool, make: Callable[[], object], log: Callable[[str], None]):
    if reuse and path.exists():
        log(f"reusing {path.name}")
        return pickle.loads(path.read_bytes())
    out = make()
    path.write_bytes(pickle.dumps(out))
    return out


def assemble_cells(phase_a: dict) -> dict:
    """{(vid, tf): {"assets": {asset: {trades, n_setups, skipped}}, "n_setups": int}}"""
    cells: dict = {}
    for asset, res in phase_a.items():
        for key, c in res["cells"].items():
            cell = cells.setdefault(key, {"assets": {}, "n_setups": 0})
            cell["assets"][asset] = c
            cell["n_setups"] += c["n_setups"]
    return cells


def merge_stability(cells: dict, phase_c: dict) -> None:
    for asset, res in phase_c.items():
        for key, rec in res["cells"].items():
            cell = cells[key]
            per = cell.setdefault("perturb", {})
            for label, v in rec["perturb"].items():
                if v is None:
                    per[label] = None
                elif per.get(label, {}) is not None:
                    per.setdefault(label, {})[asset] = v
            bk = cell.setdefault("buckets", {})
            for b, v in rec["buckets"].items():
                slot = bk.setdefault(b, {"holdout": {}, "all": {}})
                slot["holdout"][asset], slot["all"][asset] = v["holdout"], v["all"]
            cell.setdefault("frictionless", {})[asset] = rec["frictionless"]


def merge_control(cells: dict, phase_d: dict) -> None:
    for asset, res in phase_d.items():
        for key, series in res["cells"].items():
            cells[key].setdefault("control", {})[asset] = series


def cells_per_strategy(keys) -> dict:
    counts: dict = defaultdict(int)
    for vid, _ in keys:
        counts[strategy_of(vid)] += 1
    return counts


def classify_all(cells: dict) -> dict:
    n = cells_per_strategy(cells.keys())
    return {key: E.classify(cell, n[strategy_of(key[0])]) for key, cell in cells.items()}


def strategy_summary(cells: dict, verdicts: dict) -> dict:
    out = {}
    for name in strategies_in_order() + [LEGACY_GROUP]:
        keys = [k for k in cells if strategy_of(k[0]) == name]
        if not keys:
            continue
        sts = [verdicts[k]["status"] for k in keys]
        out[name] = {"status": E.best_status(sts), "cells": len(keys), "by_status": {s: sts.count(s) for s in E.STATUS_ORDER}}
    return out


def run_pipeline(assets: list[str], timeframes: tuple[str, ...], since: str, out_dir: Path, reuse: bool = False,
                 workers: int = 3, control_runs: int = E.CONTROL_RUNS, variant_ids: list[str] | None = None,
                 log: Callable[[str], None] = print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    h = f"{protocol_hash()[:10]}_{since}_{'-'.join(timeframes)}_{'-'.join(assets)}"  # caches never cross protocols
    perps = {a: PERP_FOR_ASSET[a] for a in assets}
    errors: dict = {}

    log(f"phase A: baseline runs ({len(assets)} assets x {len(timeframes)} timeframes) ...")
    a_res = _cached(out_dir / f"phase_a_{h}.pkl", reuse, lambda: _split(_run_parallel(
        runner.phase_a, {a: (p, since, timeframes, variant_ids) for a, p in perps.items()}, workers, log), errors), log)
    cells = assemble_cells(a_res)
    n_per = cells_per_strategy(cells.keys())
    log(f"  {len(cells)} cells ({time.time() - t0:.0f}s)")

    cand = [k for k, c in cells.items() if E.needs_stability_runs(c)]
    log(f"phase C: stability runs for {len(cand)} of {len(cells)} cells ...")
    if cand:
        c_res = _cached(out_dir / f"phase_c_{h}.pkl", reuse, lambda: _split(_run_parallel(
            runner.phase_c, {a: (p, since, timeframes, cand) for a, p in perps.items()}, workers, log), errors), log)
        merge_stability(cells, c_res)

    ctl = [k for k, c in cells.items() if "perturb" in c and E.needs_control(c, n_per[strategy_of(k[0])])]
    log(f"phase D: random-signal control ({control_runs} runs) for {len(ctl)} cells ...")
    if ctl:
        d_res = _cached(out_dir / f"phase_d_{h}.pkl", reuse, lambda: _split(_run_parallel(
            runner.phase_d, {a: (p, since, timeframes, ctl, control_runs) for a, p in perps.items()}, workers, log),
            errors), log)
        merge_control(cells, d_res)

    verdicts = classify_all(cells)
    info = {a: r["info"] for a, r in a_res.items()}
    log(f"done in {time.time() - t0:.0f}s")
    return {"cells": cells, "verdicts": verdicts, "summary": strategy_summary(cells, verdicts), "info": info,
            "errors": errors, "n_cells_per_strategy": dict(n_per), "since": since, "timeframes": list(timeframes),
            "control_runs": control_runs}


def _split(pair: tuple[dict, dict], errors: dict) -> dict:
    results, errs = pair
    errors.update(errs)
    return results
