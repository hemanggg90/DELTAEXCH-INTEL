"""Runs the matrix through the project's backtester. Module-level worker functions (picklable) are used by the CLI's
process pool: one worker per asset, so each loads its data once.

Nothing here fits a parameter. Every number comes from running a frozen configuration forward.
"""
from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_backtest import OptionBacktestConfig, run_option_backtest
from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.config.settings import get_settings
from delta_intelligence.config.watchlist import underlying_for
from delta_intelligence.data.data_manager import DataManager
from delta_intelligence.features.inputs import load_feature_inputs
from delta_intelligence.options.iv_history import IvHistoryStore, fit_smile
from delta_intelligence.options.selector import SelectorConfig
from delta_intelligence.strategies.legacy_v2_library import SupertrendFlip
from lab import controls
from lab.evaluate import CONTROL_RUNS, perturbed_configs
from lab.frame import BASE_TF, build_lab_frame, coverage, map_setups
from lab.library import BUCKETS, DTE_MULTIPLES, LEGACY, VARIANTS, bucket_selector, variant_by_id


@dataclass
class AssetData:
    asset: str
    perp: str
    frames: dict  # timeframe -> lab frame (>= since)
    market: OptionMarketModel
    info: dict = field(default_factory=dict)

    @property
    def frame5(self) -> pd.DataFrame:
        return self.frames[BASE_TF]


def load_market(store: IvHistoryStore, asset: str, index: pd.DataFrame):
    meta = pd.read_parquet(store.meta_path())
    meta = meta[meta["underlying"] == asset]
    hourly = pd.read_parquet(store.root / "iv_obs" / f"{asset}_atm_hourly.parquet")
    obs = pd.read_parquet(store.obs_path(asset), columns=["known_at", "t_hours", "log_moneyness", "iv"])
    smile = fit_smile(obs, hourly)
    strikes = {e: np.sort(g["strike"].unique()) for e, g in meta.groupby("expiry")}
    symbols = {(k, s, e): sym for k, s, e, sym in zip(meta["kind"], meta["strike"], meta["expiry"], meta["symbol"])}
    return OptionMarketModel(asset, index, hourly, smile, strikes, symbols), hourly, meta


def load_asset(perp: str, since: str, timeframes: tuple[str, ...]) -> AssetData:
    s = get_settings()
    dm = DataManager.from_settings(s)
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    u = underlying_for(perp)
    start = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc)
    inp = load_feature_inputs(dm, perp, BASE_TF, start - dt.timedelta(days=3))
    market, hourly, meta = load_market(store, u.asset, inp.aux.index)
    frames, info = {}, {"asset": u.asset, "perp": perp}
    for tf in timeframes:
        f = build_lab_frame(inp.ohlcv, inp.aux, tf, hourly)
        f = f[f["timestamp"] >= pd.Timestamp(start)].reset_index(drop=True)
        frames[tf] = f
        info[tf] = coverage(f)
    exp = pd.to_datetime(meta["expiry"], utc=True)
    info["options"] = {"expiries": int(exp.nunique()), "first_expiry": str(exp.min()), "last_expiry": str(exp.max())}
    info["aux_errors"] = dict(inp.aux_errors)
    info["quality"] = inp.quality_status
    return AssetData(u.asset, perp, frames, market, info)


def result_to_cell(res, data: "AssetData | None" = None) -> dict:
    if data is not None and "regime" in data.frame5:  # tag each trade with the project's regime label at entry
        reg = data.frame5["regime"].to_numpy()
        for t in res.trades:
            t.regime = str(reg[t.entry_idx])
    return {"trades": res.trades, "n_setups": res.n_setups, "skipped": dict(res.skipped)}


@contextlib.contextmanager
def frictionless(market: OptionMarketModel):
    spread, tick = market.spread, market.tick
    market.spread, market.tick = (0.0, 0.0), 1e-9
    try:
        yield
    finally:
        market.spread, market.tick = spread, tick


def _legacy_strategy(config_target: float = 2.25) -> SupertrendFlip:
    return SupertrendFlip({"target_atr_mult": config_target})


def build_for(variant_id: str, tf: str):
    """(strategy, baseline selector, timeframe) for a variant id or a LEGACY id."""
    for lid, mny in LEGACY:
        if variant_id == lid:
            return _legacy_strategy(), SelectorConfig(moneyness=mny), BASE_TF
    v = variant_by_id(variant_id)
    return v.build(tf), bucket_selector("ATM_040_060"), tf


def backtest(data: AssetData, strategy, tf: str, selector: SelectorConfig, setups=None, **cfg):
    f = data.frames[tf]
    if setups is None:
        setups = map_setups(strategy.historical_setups(f), tf)
    return run_option_backtest(strategy, data.frame5, data.market, OptionBacktestConfig(selector=selector, **cfg),
                               setups=setups)


def phase_a(perp: str, since: str, timeframes: tuple[str, ...], variant_ids: list[str] | None = None) -> dict:
    """Baseline run of every variant on every timeframe for one asset. Returns {asset, info, cells}."""
    data = load_asset(perp, since, timeframes)
    ids = variant_ids or [v.id for v in VARIANTS]
    cells: dict = {}
    for vid in [i for i in ids if not i.startswith("LEGACY")]:
        for tf in timeframes:
            strat, sel, _ = build_for(vid, tf)
            res = backtest(data, strat, tf, sel)
            cells[(vid, tf)] = result_to_cell(res, data)
    # the project's two active (weak) Supertrend variants, 5m only, through the same pipeline
    for lid, mny in LEGACY:
        if variant_ids is None or lid in variant_ids:
            strat, sel, tf = build_for(lid, BASE_TF)
            cells[(lid, BASE_TF)] = result_to_cell(backtest(data, strat, tf, sel), data)
    return {"asset": data.asset, "info": data.info, "cells": cells}


def _sum_n(trades) -> tuple[float, int]:
    return float(sum(t.net_r for t in trades)), len(trades)


def phase_c(perp: str, since: str, timeframes: tuple[str, ...], jobs: list[tuple[str, str]]) -> dict:
    """Stability runs for the candidate cells (variant id, timeframe): +-20% perturbations, option buckets, DTE
    sensitivity and a frictionless run. Returns {asset, cells: {(vid, tf): {...}}} with (sum_r, n) pairs."""
    from lab.evaluate import HOLDOUT_START

    data = load_asset(perp, since, timeframes)
    out: dict = {}
    for vid, tf in jobs:
        strat, sel, _ = build_for(vid, tf)
        rec: dict = {"perturb": {}, "buckets": {}, "frictionless": None}
        # perturbations (baseline bucket)
        if vid.startswith("LEGACY"):
            for fct in (0.8, 1.2):
                p = _legacy_strategy(2.25 * fct)
                rec["perturb"][f"target_atr_multx{fct}"] = _sum_n(backtest(data, p, BASE_TF, sel).trades)
        else:
            for label, cfg in perturbed_configs(variant_by_id(vid).config):
                if cfg is None:
                    rec["perturb"][label] = None
                    continue
                p = variant_by_id(vid).strategy_cls(cfg, tf)
                rec["perturb"][label] = _sum_n(backtest(data, p, tf, sel).trades)
        # option-selection buckets and DTE sensitivity (same signals every time)
        setups = map_setups(strat.historical_setups(data.frames[tf]), tf)
        for b in BUCKETS:
            for dte in DTE_MULTIPLES if b == "ATM_040_060" else (2.5,):
                res = backtest(data, strat, tf, bucket_selector(b, dte), setups=setups)
                hold = [t for t in res.trades if t.entry_time >= HOLDOUT_START]
                rec["buckets"][f"{b}@{dte}"] = {"holdout": _sum_n(hold), "all": _sum_n(res.trades)}
        with frictionless(data.market):
            res = backtest(data, strat, tf, sel, setups=setups, commission_rate=0, premium_cap_rate=0, gst_rate=0,
                           apply_breakeven_gate=False)
            rec["frictionless"] = _sum_n(res.trades)
        out[(vid, tf)] = rec
    return {"asset": data.asset, "cells": out}


def phase_d(perp: str, since: str, timeframes: tuple[str, ...], jobs: list[tuple[str, str]], runs: int = CONTROL_RUNS,
            seed: int = 20261002) -> dict:
    """Random-signal control for cells that passed every other gate. Returns {asset, cells: {(vid, tf): [(sum, n)]}}."""
    data = load_asset(perp, since, timeframes)
    out: dict = {}
    for vid, tf in jobs:
        strat, sel, _ = build_for(vid, tf)
        base = map_setups(strat.historical_setups(data.frames[tf]), tf)
        rng = np.random.default_rng(seed)
        series = []
        for _ in range(runs):
            if getattr(strat, "is_vol", False):
                s = controls.random_entry_setups(strat, data.frames[tf], len(base), rng)
                s = map_setups(s, tf)
            else:
                s = controls.randomize_directions(base, rng)
            series.append(_sum_n(backtest(data, strat, tf, sel, setups=s).trades))
        out[(vid, tf)] = series
    return {"asset": data.asset, "cells": out}
