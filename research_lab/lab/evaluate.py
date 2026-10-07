"""Pure evaluation logic: splits, gates and status. No data access, so it is fully unit-testable.

Frozen acceptance protocol (identical in spirit to the project's rules; nothing is lowered):
- DATA-INSUFFICIENT: fewer than 200 pooled trades, or fewer than 30 trades in the frozen holdout. The threshold is not
  lowered; the report shows how many setups and trades there were.
- REJECTED: enough trades, but pooled net R in the frozen holdout is not positive after costs.
- ACCEPTED: all of: holdout net R > 0; every +-20% parameter perturbation keeps pooled net R > 0; >= 2 assets with
  >= 30 trades and positive net R; positive holdout net R in >= 2 of the 3 option-selection buckets; and a significant
  edge over random-direction entries at the same times (Bonferroni-adjusted over the strategy's cells).
- EXPERIMENTAL: holdout net R > 0 but at least one of the other gates failed. Not proven, shown with the reasons.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from lab.config import BaseConfig, ConfigError

ACCEPTED, REJECTED, DATA_INSUFFICIENT, EXPERIMENTAL = "ACCEPTED", "REJECTED", "DATA-INSUFFICIENT", "EXPERIMENTAL"
STATUS_ORDER = (ACCEPTED, EXPERIMENTAL, REJECTED, DATA_INSUFFICIENT)  # best to worst, for a strategy-level summary

HOLDOUT_START = pd.Timestamp("2026-07-01", tz="UTC")  # frozen: discovery before, holdout from this date
MIN_TRADES, MIN_PER_ASSET, MIN_ASSETS, MIN_HOLDOUT = 200, 30, 2, 30
CONTROL_ALPHA, CONTROL_RUNS, N_FOLDS = 0.05, 300, 4
MIN_BUCKETS_POSITIVE = 2


def mean_r(trades) -> float | None:
    return float(np.mean([t.net_r for t in trades])) if trades else None


def pooled(by_asset: dict) -> list:
    return sorted((t for a in by_asset.values() for t in a["trades"]), key=lambda t: t.entry_time)


def split_holdout(trades) -> tuple[list, list]:
    return ([t for t in trades if t.entry_time < HOLDOUT_START], [t for t in trades if t.entry_time >= HOLDOUT_START])


def pooled_sum_n(parts: dict) -> tuple[float, int]:
    """parts: asset -> (sum_r, n)."""
    s = sum(v[0] for v in parts.values())
    n = sum(v[1] for v in parts.values())
    return s, n


def pooled_mean(parts: dict) -> float | None:
    s, n = pooled_sum_n(parts)
    return s / n if n else None


def perturbed_configs(config: BaseConfig) -> list[tuple[str, BaseConfig | None]]:
    """+-20% of each key parameter. Integers are rounded (and moved by 1 if rounding changes nothing). A perturbed
    value the config rejects (e.g. a percentile above 1) is returned as None and reported, not silently clipped."""
    out: list[tuple[str, BaseConfig | None]] = []
    for k in config.key_params:
        base = getattr(config, k)
        for fct in (0.8, 1.2):
            if isinstance(base, int) and not isinstance(base, bool):
                v = int(round(base * fct))
                if v == base:
                    v = base + (1 if fct > 1 else -1)
            else:
                v = base * fct
            try:
                out.append((f"{k}x{fct}", config.with_(**{k: v})))
            except ConfigError:
                out.append((f"{k}x{fct}", None))
    return out


def control_pvalue(observed: float, null_means: list[float]) -> float:
    """One-sided p: share of random-direction runs (plus the observed one) with a mean net R at least as good."""
    arr = np.asarray([x for x in null_means if x is not None and np.isfinite(x)], float)
    return float((1 + (arr >= observed).sum()) / (1 + len(arr)))


def control_null_means(control: dict) -> list[float]:
    """control: asset -> list over seeds of (sum_r, n). Pools assets per seed."""
    assets = list(control)
    if not assets:
        return []
    seeds = min(len(control[a]) for a in assets)
    out = []
    for k in range(seeds):
        s = sum(control[a][k][0] for a in assets)
        n = sum(control[a][k][1] for a in assets)
        if n:
            out.append(s / n)
    return out


def gate_results(cell: dict, n_cells: int) -> dict:
    """Evaluate every gate that has data. Returns {gate: (passed|None, detail)}; None = not run."""
    by_asset = cell["assets"]
    trades = pooled(by_asset)
    disc, hold = split_holdout(trades)
    g: dict = {}
    g["trades"] = (len(trades) >= MIN_TRADES, f"{len(trades)} trades (need {MIN_TRADES})")
    g["holdout_n"] = (len(hold) >= MIN_HOLDOUT, f"{len(hold)} holdout trades (need {MIN_HOLDOUT})")
    hr = mean_r(hold)
    g["holdout_r"] = (hr is not None and hr > 0, f"holdout net R {fmt(hr)}")
    per = cell.get("perturb")
    if per is None:
        g["perturbation"] = (None, "not run")
    else:
        vals = {k: pooled_mean(v) for k, v in per.items() if v is not None}
        bad = [k for k, v in vals.items() if v is None or v <= 0]
        g["perturbation"] = (bool(vals) and not bad,
                             f"{len(vals) - len(bad)}/{len(vals)} perturbations positive"
                             + (f" (failing: {', '.join(bad)})" if bad else ""))
    good = [a for a, r in by_asset.items() if len(r["trades"]) >= MIN_PER_ASSET and (mean_r(r["trades"]) or -1) > 0]
    g["assets"] = (len(good) >= MIN_ASSETS, f"positive on {len(good)} asset(s) with >= {MIN_PER_ASSET} trades"
                   + (f": {', '.join(good)}" if good else ""))
    bk = cell.get("buckets")
    if bk is None:
        g["buckets"] = (None, "not run")
    else:
        pos = {b: (pooled_mean(v["holdout"]) or -1) > 0 for b, v in bk.items() if b.endswith("@2.5")}
        g["buckets"] = (sum(pos.values()) >= MIN_BUCKETS_POSITIVE,
                        f"holdout net R positive in {sum(pos.values())}/{len(pos)} option buckets")
    ctl = cell.get("control")
    if ctl is None:
        g["control"] = (None, "not run (an earlier gate already decided the status)")
    else:
        null = control_null_means(ctl)
        p = control_pvalue(mean_r(trades) or 0.0, null)
        p_adj = min(1.0, p * n_cells)
        g["control"] = (p_adj <= CONTROL_ALPHA, f"p={p:.4f} vs {len(null)} random runs, adjusted for {n_cells} cells "
                        f"= {p_adj:.4f} (need <= {CONTROL_ALPHA})")
    return g


def classify(cell: dict, n_cells: int) -> dict:
    g = gate_results(cell, n_cells)
    reasons: list[str] = []
    if not g["trades"][0]:
        status = DATA_INSUFFICIENT
        reasons.append(g["trades"][1] + f"; {cell.get('n_setups', 0)} setups")
    elif not g["holdout_n"][0]:
        status = DATA_INSUFFICIENT
        reasons.append(g["holdout_n"][1])
    elif not g["holdout_r"][0]:
        status = REJECTED
        reasons.append(g["holdout_r"][1] + " (not positive after costs)")
    else:
        failed = [(k, d) for k, (ok, d) in g.items() if ok is False]
        not_run = [k for k, (ok, _) in g.items() if ok is None]
        if not failed and not not_run:
            status = ACCEPTED
        else:
            status = EXPERIMENTAL
            reasons += [f"{k}: {d}" for k, d in failed]
            reasons += [f"{k}: not run" for k in not_run]
    return {"status": status, "reasons": reasons, "gates": {k: {"ok": ok, "detail": d} for k, (ok, d) in g.items()}}


def needs_stability_runs(cell: dict) -> bool:
    """Perturbation / bucket runs are only worth running for cells that already have >= 200 trades and a positive
    holdout; every other cell's status is final."""
    g = gate_results({**cell, "perturb": None, "buckets": None, "control": None}, 1)
    return bool(g["trades"][0] and g["holdout_n"][0] and g["holdout_r"][0])


def needs_control(cell: dict, n_cells: int) -> bool:
    g = gate_results(cell, n_cells)
    return all(g[k][0] for k in ("trades", "holdout_n", "holdout_r", "perturbation", "assets", "buckets"))


def best_status(statuses: list[str]) -> str:
    return min(statuses, key=STATUS_ORDER.index) if statuses else DATA_INSUFFICIENT


def fmt(x, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"{x:+.{nd}f}"
