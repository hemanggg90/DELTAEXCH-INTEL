"""Cached research helpers for the dashboard: features, the strategy context and the option market model, built once
per process (Streamlit cache) from the local candle / IV caches."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS
from delta_intelligence.ui import state
from delta_intelligence.utils.timeutil import now_utc


@st.cache_data(ttl=600, show_spinner="Computing features...")
def features(perp: str, days: int):
    from delta_intelligence.features.feature_engine import compute_features
    from delta_intelligence.features.inputs import load_feature_inputs

    end = now_utc()
    if state.offline():
        df = state.candles(perp, "5m", days)
        if not len(df):
            return None, None, "no cached data"
        from delta_intelligence.features.feature_engine import AuxData

        return df, compute_features(df, "5m", AuxData()), "OFFLINE (cache only)"
    inp = load_feature_inputs(state.data_manager(), perp, "5m", end - dt.timedelta(days=days), end)
    return inp.ohlcv, compute_features(inp.ohlcv, "5m", inp.aux), inp.quality_status


@st.cache_data(ttl=600, show_spinner="Building strategy context...")
def strategy_frame(perp: str, days: int):
    from delta_intelligence.config.events import load_events
    from delta_intelligence.strategies.context import build_strategy_frame

    ohlcv, feats, _ = features(perp, days)
    if ohlcv is None:
        return None
    hourly = _hourly(UNDERLYINGS[perp].asset)
    return build_strategy_frame(ohlcv, feats, hourly, load_events())


def _hourly(asset: str) -> pd.DataFrame | None:
    s = get_settings()
    p = s.data_cache_dir / "options" / s.data_env.lower() / "iv_obs" / f"{asset}_atm_hourly.parquet"
    return pd.read_parquet(p) if p.exists() else None


@st.cache_resource(show_spinner="Loading option market model...")
def market(asset: str, perp: str, days: int):
    """OptionMarketModel + real-price loader from the IV-history caches (None when not built)."""
    from delta_intelligence.backtesting.option_backtest import RealOptionPrices
    from delta_intelligence.backtesting.option_market import OptionMarketModel
    from delta_intelligence.options.iv_history import IvHistoryStore, fit_smile

    s = get_settings()
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    if not store.meta_path().exists() or not store.obs_path(asset).exists():
        return None, None
    meta = pd.read_parquet(store.meta_path())
    meta = meta[meta["underlying"] == asset]
    hourly = _hourly(asset)
    obs = pd.read_parquet(store.obs_path(asset), columns=["known_at", "t_hours", "log_moneyness", "iv"])
    index = state.candles(UNDERLYINGS[perp].index_symbol, "5m", days + 2)
    strikes = {e: np.sort(g["strike"].unique()) for e, g in meta.groupby("expiry")}
    symbols = {(k, s_, e): sym for k, s_, e, sym in zip(meta["kind"], meta["strike"], meta["expiry"], meta["symbol"])}

    def loader(expiry):
        p = store.expiry_path(asset, expiry)
        return pd.read_parquet(p) if p.exists() else None

    return OptionMarketModel(asset, index, hourly, fit_smile(obs, hourly), strikes, symbols), RealOptionPrices(loader)
