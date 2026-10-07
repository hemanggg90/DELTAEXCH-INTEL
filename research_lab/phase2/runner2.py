"""Phase 2 workers: the same frozen signals pushed through many option-selection policies, and through REAL_ONLY pricing
when recorded option quotes overlap them. One worker per asset (data loaded once). Nothing here fits anything."""
from __future__ import annotations

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_backtest import (MODEL_ONLY, REAL_ONLY, REAL_THEN_MODEL_FALLBACK,
                                                            OptionBacktestConfig, run_option_backtest)
from delta_intelligence.backtesting.recorded_chain import RecordedQuotes
from delta_intelligence.options import chain_quality as cq
from lab import controls, runner
from lab.evaluate import HOLDOUT_START, perturbed_configs
from lab.frame import BASE_TF, map_setups
from lab.library import variant_by_id
from phase2.policies import BY_ID, POLICIES, make_picker

DELTA_POLICIES = ("DELTA-BROAD-030-060", "DELTA-ITM-050-070", "DELTA-OTM-020-040")


def applicable(policy_id: str, strategy) -> bool:
    """Delta bands mean nothing to a straddle (it is always ATM), and real-liquidity policies cannot run on modelled
    data. Those combinations are not run, not faked."""
    p = BY_ID[policy_id]
    if p.needs_real_liquidity:
        return False
    if getattr(strategy, "is_vol", False) and policy_id in DELTA_POLICIES:
        return False
    return True


def backtest2(data, strategy, tf: str, base_selector, policy_id: str, setups=None, mode: str = MODEL_ONLY,
              recorded: RecordedQuotes | None = None, **cfg):
    p = BY_ID[policy_id]
    picker = make_picker(p)
    f = data.frames[tf]
    if setups is None:
        setups = map_setups(strategy.historical_setups(f), tf)
    c = OptionBacktestConfig(selector=base_selector, pricing_mode=mode, contract_picker=picker, selection_name=policy_id,
                             **cfg)
    return run_option_backtest(strategy, data.frame5, data.market, c, setups=setups, recorded=recorded)


def _cell(res) -> dict:
    return {"trades": res.trades, "n_setups": res.n_setups, "skipped": dict(res.skipped),
            "unpriced_exits": list(res.unpriced_exits)}


def _sum_n(trades) -> tuple[float, int]:
    return float(sum(t.net_r for t in trades)), len(trades)


def spill_path(spill_dir: str, asset: str, vid: str, tf: str, pid: str):
    from pathlib import Path

    return Path(spill_dir) / asset / f"{vid}__{tf}__{pid}.pkl"


def load_spilled(spill_dir: str, asset: str, vid: str, tf: str, pid: str) -> list:
    import pickle

    p = spill_path(spill_dir, asset, vid, tf, pid)
    return pickle.loads(p.read_bytes()) if p.exists() else []


def matrix(perp: str, since: str, timeframes: tuple, cells: list, policy_ids: list, spill_dir: str) -> dict:
    """{(vid, tf, pid): summary} on MODELLED prices (MODEL_ONLY), every applicable policy, same signals.

    Memory: trades are written to disk (`spill_dir`) as soon as a (cell, policy) finishes, and only compact, poolable
    summaries are returned, so neither this worker nor the parent ever holds every policy's trades at once."""
    import gc
    import pickle

    from phase2 import analysis as A

    data = runner.load_asset(perp, since, timeframes)
    out: dict = {}
    for vid, tf in cells:
        strat, sel, _ = runner.build_for(vid, tf)
        setups = map_setups(strat.historical_setups(data.frames[tf]), tf)
        for pid in policy_ids:
            if not applicable(pid, strat):
                continue
            res = backtest2(data, strat, tf, sel, pid, setups=setups)
            trades = res.trades
            d, h = A.split(trades)
            p = spill_path(spill_dir, data.asset, vid, tf, pid)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(pickle.dumps(trades, protocol=pickle.HIGHEST_PROTOCOL))
            out[(vid, tf, pid)] = {"n_setups": res.n_setups, "skipped": dict(res.skipped), "periods": A.period_metrics(trades),
                                   "buckets": A.bucket_sums(trades), "disc": (len(d), A._m(t.net_r for t in d)),
                                   "hold": (len(h), A._m(t.net_r for t in h)), "hold_sum_n": _sum_n(h)}
            del res, trades, d, h
        gc.collect()
    return {"asset": data.asset, "info": data.info, "cells": out}


def stability(perp: str, since: str, timeframes: tuple, jobs: list) -> dict:
    """+-20% perturbations of the signal's key parameters with the SELECTED policy frozen. jobs: (vid, tf, pid)."""
    data = runner.load_asset(perp, since, timeframes)
    out: dict = {}
    for vid, tf, pid in jobs:
        _, sel, _ = runner.build_for(vid, tf)
        per: dict = {}
        if vid.startswith("LEGACY"):
            for fct in (0.8, 1.2):
                p = runner._legacy_strategy(2.25 * fct)
                per[f"target_atr_multx{fct}"] = _sum_n(backtest2(data, p, BASE_TF, sel, pid).trades)
        else:
            v = variant_by_id(vid)
            for label, cfg in perturbed_configs(v.config):
                per[label] = None if cfg is None else _sum_n(backtest2(data, v.strategy_cls(cfg, tf), tf, sel, pid).trades)
        out[(vid, tf, pid)] = per
    return {"asset": data.asset, "cells": out}


def control(perp: str, since: str, timeframes: tuple, jobs: list, runs: int, seed: int = 20261003) -> dict:
    data = runner.load_asset(perp, since, timeframes)
    out: dict = {}
    for vid, tf, pid in jobs:
        strat, sel, _ = runner.build_for(vid, tf)
        base = map_setups(strat.historical_setups(data.frames[tf]), tf)
        rng = np.random.default_rng(seed)
        series = []
        for _ in range(runs):
            if getattr(strat, "is_vol", False):
                s = map_setups(controls.random_entry_setups(strat, data.frames[tf], len(base), rng), tf)
            else:
                s = controls.randomize_directions(base, rng)
            series.append(_sum_n(backtest2(data, strat, tf, sel, pid, setups=s).trades))
        out[(vid, tf, pid)] = series
    return {"asset": data.asset, "cells": out}


def real_stage(perp: str, since: str, timeframes: tuple, jobs: list, max_age_sec: float = 600.0) -> dict:
    """REAL_ONLY (and the labelled fallback mode) wherever recorded option quotes exist. jobs: (vid, tf, pid).
    Also: data quality, coverage and the model-vs-real quote comparison for this asset."""
    from phase2 import analysis as A

    data = runner.load_asset(perp, since, timeframes)
    raw = cq.load_snapshots(underlying=data.asset)
    assessed, qrep = cq.assess(raw)
    info = {"asset": data.asset, "n_rows": int(len(assessed)), "n_snapshots": qrep.n_snapshots,
            "flag_counts": qrep.flag_counts, "tier_counts": qrep.tier_counts, "gaps": qrep.gaps,
            "median_interval_sec": qrep.median_interval_sec, "notes": qrep.notes}
    out: dict = {"asset": data.asset, "quality": info, "cells": {}, "model_vs_real": None, "coverage": None}
    if assessed.empty:
        return out
    cov = cq.coverage(assessed)
    out["coverage"] = {k: v for k, v in cov.items()}
    rec = RecordedQuotes(assessed, data.asset, max_age_sec)
    win = rec.window()
    info["window"] = None if win is None else (str(win[0]), str(win[1]))
    info["snapshot_days"] = 0 if win is None else int((win[1] - win[0]).total_seconds() // 86400) + 1
    out["model_vs_real"] = A.model_vs_real(assessed, data.market)
    for vid, tf, pid in jobs:
        strat, sel, _ = runner.build_for(vid, tf)
        setups = map_setups(strat.historical_setups(data.frames[tf]), tf)
        # only signals whose entry time can be covered by a recorded snapshot are worth evaluating
        lo, hi = win[0] - pd.Timedelta(seconds=max_age_sec), win[1] + pd.Timedelta(minutes=1)
        inside = [s for s in setups if lo <= pd.Timestamp(s.timestamp) + pd.Timedelta(minutes=5) <= hi]
        rec_cell = {"n_signals_in_window": len(inside), "n_signals_total": len(setups)}
        for mode in (REAL_ONLY, REAL_THEN_MODEL_FALLBACK):
            res = backtest2(data, strat, tf, sel, pid, setups=inside, mode=mode, recorded=rec)
            rec_cell[mode] = _cell(res)
        out["cells"][(vid, tf, pid)] = rec_cell
    return out
