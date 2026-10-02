"""
Shared, process-wide resources for the Streamlit app.

- `get_engine()` holds the trading engine via `st.cache_resource`, so it survives reruns, page switches and closed
  tabs. It is NOT started automatically: the user starts it, behind the password gate. Only one engine may run per
  database: a fresh heartbeat from any other engine disables Start.
- Market data comes from the public client with process-wide caches. Every tab shares one chain request every few
  seconds instead of hitting Delta per rerun.
- `UI_OFFLINE=1` (used by the UI smoke test) makes every network-backed helper return empty data instead of calling
  Delta.

UI state lives in st.session_state; trading state lives in the database.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.database import db
from delta_intelligence.options.chain import OptionChain, fetch_chain
from delta_intelligence.utils.timeutil import now_utc


def offline() -> bool:
    return os.getenv("UI_OFFLINE", "").strip() in ("1", "true", "yes")


@st.cache_resource(show_spinner=False)
def _init_db() -> bool:
    db.init_db()
    return True


def ensure_db() -> None:
    _init_db()


@st.cache_resource(show_spinner=False)
def public():
    from delta_intelligence.brokers.delta_api_client import public_client

    return public_client(get_settings())


@st.cache_resource(show_spinner=False)
def data_manager():
    from delta_intelligence.data.data_manager import DataManager

    return DataManager.from_settings(get_settings())


def assets() -> tuple[str, ...]:
    return tuple(UNDERLYINGS[p].asset for p in get_watchlist())


def chain() -> OptionChain:
    """The live option chain (one cached REST call shared process-wide; 15 s). Empty offline or on error."""
    if offline():
        return OptionChain([])
    try:
        return fetch_chain(public(), assets(), max_age=15)
    except Exception as exc:
        st.session_state["_chain_error"] = str(exc)
        return OptionChain([])


def tickers() -> dict:
    if offline():
        return {}
    try:
        return public().get_tickers(max_age=5, stale_ok_for=60)
    except Exception:
        return {}


@st.cache_resource(show_spinner=False)
def get_engine():
    """The paper engine for THIS Streamlit process (not started here)."""
    from delta_intelligence.brokers.paper_broker import PaperBroker
    from delta_intelligence.brokers.ws_feed import TickerFeed
    from delta_intelligence.execution.engine import TradingEngine

    s = get_settings()
    ensure_db()
    chain_fn = lambda: fetch_chain(public(), assets(), max_age=15)  # noqa: E731
    broker = PaperBroker(chain_fn, s)
    feed = TickerFeed(s.ws_url(private=False), list(get_watchlist()))
    return TradingEngine(broker, chain_fn, data_manager(), s, feed=feed, iv_history=iv_history())


def broker():
    from delta_intelligence.brokers.paper_broker import PaperBroker

    ensure_db()
    return PaperBroker(chain, get_settings())


@st.cache_data(ttl=3600, show_spinner=False)
def iv_history() -> dict:
    s = get_settings()
    out = {}
    root = s.data_cache_dir / "options" / s.data_env.lower() / "iv_obs"
    for u in UNDERLYINGS.values():
        p = root / f"{u.asset}_atm_hourly.parquet"
        if p.exists():
            h = pd.read_parquet(p).dropna(subset=["atm_iv_6_30h"])
            out[u.asset] = pd.Series(h["atm_iv_6_30h"].to_numpy(), index=pd.to_datetime(h["available_at"], utc=True))
    return out


def engine_heartbeat() -> tuple[dt.datetime | None, float | None]:
    ensure_db()
    hb = db.get_state("engine_heartbeat")
    if not hb:
        return None, None
    t = dt.datetime.fromisoformat(hb)
    return t, (now_utc() - t).total_seconds()


def external_engine_running() -> bool:
    """True when some engine (headless script or another app process) is heartbeating, and it isn't ours."""
    _, age = engine_heartbeat()
    ours = get_engine().is_running() if not offline() else False
    return age is not None and age < 120 and not ours


@st.cache_data(ttl=300, show_spinner=False)
def candles(symbol: str, timeframe: str, days: float) -> pd.DataFrame:
    """Closed candles from the cache/Delta (cached 5 min). Empty offline when nothing is cached."""
    dm = data_manager()
    end = now_utc()
    try:
        if offline():
            path = dm.cache_path(symbol, timeframe)
            if not path.exists():
                return pd.DataFrame()
            df = pd.read_parquet(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            return df[df["timestamp"] >= end - dt.timedelta(days=days)].reset_index(drop=True)
        df, _ = dm.get_ohlcv(symbol, timeframe, end - dt.timedelta(days=days), end)
        return df
    except Exception:
        return pd.DataFrame()
