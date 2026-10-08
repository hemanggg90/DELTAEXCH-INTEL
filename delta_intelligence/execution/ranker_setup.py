"""Shared wiring of the live ranker and the live IV history for every place that builds a TradingEngine (dashboard, headless
runner). One definition, so the dashboard and `scripts/run_engine.py` behave the same.

`RANKER_MODE` (environment / Streamlit secrets): `top` (default), `select`, `shadow` or `off`.
- top: the best-ranked strategy that is signalling (with evidence and a positive edge estimate) trades, trying the next-ranked one
  if a trade is rejected; PAPER only. It can trade below the strict evidence bar, and the dashboard says so;
- select: the STRICT ranker's choice is the only strategy that may trade, and only for a PAPER broker;
- shadow: ranked and displayed, while the two always-on Supertrend variants keep trading as before;
- off: no ranker.
In LIVE mode `top` and `select` automatically become `shadow` (see `TradingEngine.effective_ranker_mode`).
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from delta_intelligence.options import iv_live
from delta_intelligence.ranking.live_ranker import LiveRanker
from delta_intelligence.strategies.universe import UNIVERSE

VALID_MODES = ("top", "select", "shadow", "off")


def ranker_mode() -> str:
    m = os.getenv("RANKER_MODE", "top").strip().lower()
    return m if m in VALID_MODES else "top"


def iv_parquet_root(settings) -> Path:
    return settings.data_cache_dir / "options" / settings.data_env.lower() / "iv_obs"


def make_iv_history_fn(settings, ttl_sec: float = 300.0) -> Callable[[str], pd.Series]:
    """asset -> ATM IV history (local parquet + committed seed + recorded snapshots), cached for `ttl_sec`."""
    cache: dict = {}
    root = iv_parquet_root(settings)

    def fn(asset: str) -> pd.Series:
        hit = cache.get(asset)
        if hit and time.monotonic() - hit[0] < ttl_sec:
            return hit[1]
        series, _ = iv_live.merged_history(asset, root)
        cache[asset] = (time.monotonic(), series)
        return series

    return fn


def engine_kwargs(settings) -> dict:
    """Keyword arguments for `TradingEngine` that turn the live ranker and the live IV history on."""
    mode = ranker_mode()
    kw = {"iv_history_fn": make_iv_history_fn(settings)}
    if mode != "off":
        kw.update(ranker=LiveRanker.from_file(), universe=UNIVERSE, ranker_mode=mode)
    return kw
