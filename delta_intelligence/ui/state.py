"""
Shared, process-wide resources for the Streamlit app.

- `get_engine()` holds the trading engine via `st.cache_resource`, so it survives reruns, page switches and closed
  tabs. In LIVE mode it is None until the Live page builds it behind the startup gate. It is NOT started
  automatically: the user starts it, behind the password gate. Only one engine may run per
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
def _paper_engine():
    from delta_intelligence.brokers.paper_broker import PaperBroker
    from delta_intelligence.brokers.ws_feed import TickerFeed
    from delta_intelligence.execution.engine import TradingEngine

    s = get_settings()
    ensure_db()
    chain_fn = lambda: fetch_chain(public(), assets(), max_age=15)  # noqa: E731
    broker = PaperBroker(chain_fn, s)
    feed = TickerFeed(s.ws_url(private=False), list(get_watchlist()))
    from delta_intelligence.execution.ranker_setup import engine_kwargs

    return TradingEngine(broker, chain_fn, data_manager(), s, feed=feed, iv_history=iv_history(), **engine_kwargs(s))


@st.cache_resource(show_spinner=False)
def _live_box() -> dict:
    """Holds the LIVE engine once the user has built it on the Live page. Never built implicitly."""
    return {"engine": None}


def live_mode() -> bool:
    return get_settings().is_live_mode


def get_engine():
    """The engine for THIS Streamlit process (not started here). PAPER: always available. LIVE: None until the
    Live page has passed the startup gate and built it (`build_live_engine`)."""
    if live_mode():
        return _live_box()["engine"]
    return _paper_engine()


def build_live_engine():
    """Run the LIVE startup gate (double gate + read-only connectivity check) and build the live engine. Raises
    LiveGateError when anything is unmet. The engine is returned stopped; the caller starts it."""
    from delta_intelligence.brokers.live_gate import build_live_broker
    from delta_intelligence.brokers.ws_feed import TickerFeed
    from delta_intelligence.execution.engine import TradingEngine

    box = _live_box()
    if box["engine"] is not None:
        return box["engine"]
    s = get_settings()
    ensure_db()
    chain_fn = lambda: fetch_chain(public(), assets(), max_age=15)  # noqa: E731
    broker = build_live_broker(chain_fn, s)
    feed = TickerFeed(s.ws_url(private=False), list(get_watchlist()))
    from delta_intelligence.execution.ranker_setup import engine_kwargs

    box["engine"] = TradingEngine(broker, chain_fn, data_manager(), s, feed=feed, iv_history=iv_history(),
                                   **engine_kwargs(s))  # LIVE: the ranker can only watch (select -> shadow)
    return box["engine"]


def paper_broker():
    """Always the paper book (the manual paper ticket must never reach a live broker)."""
    from delta_intelligence.brokers.paper_broker import PaperBroker

    ensure_db()
    return PaperBroker(chain, get_settings())


def broker():
    """The ACTIVE book: the live broker once the live engine is built in LIVE mode, otherwise the paper book."""
    eng = _live_box()["engine"] if live_mode() else None
    return eng.broker if eng is not None else paper_broker()


@st.cache_data(ttl=300, show_spinner=False)
def iv_history() -> dict:
    """asset -> ATM IV history from every REAL source (local parquet, committed seed, the recorder's own snapshots)."""
    from delta_intelligence.options import iv_live

    s = get_settings()
    root = s.data_cache_dir / "options" / s.data_env.lower() / "iv_obs"
    out = {}
    for u in UNDERLYINGS.values():
        series, _ = iv_live.merged_history(u.asset, root)
        if len(series):
            out[u.asset] = series
    return out


@st.cache_data(ttl=300, show_spinner=False)
def iv_sources() -> dict:
    """asset -> {n_obs, sources} for the 'where does the IV percentile come from' captions."""
    from delta_intelligence.options import iv_live

    s = get_settings()
    root = s.data_cache_dir / "options" / s.data_env.lower() / "iv_obs"
    return {u.asset: iv_live.merged_history(u.asset, root)[1] for u in UNDERLYINGS.values()}


def engine_heartbeat() -> tuple[dt.datetime | None, float | None]:
    ensure_db()
    hb = db.get_state("engine_heartbeat")
    if not hb:
        return None, None
    t = dt.datetime.fromisoformat(hb)
    return t, (now_utc() - t).total_seconds()


def external_engine_owner() -> dict:
    """The registered engine owner record (host:pid, started_at, and on newer engines mode / ranker_mode / version)."""
    from delta_intelligence.execution.engine import OWNER_KEY

    ensure_db()
    return db.get_state(OWNER_KEY, {}) or {}


def orphan_engine_in_process() -> bool:
    """True when an engine thread from an earlier version of this app (before a redeploy) is still running in this process."""
    from delta_intelligence.execution.engine import other_engines_in_process

    if offline():
        return False
    return bool(other_engines_in_process(get_engine()))


def external_engine_running() -> bool:
    """True when some engine (headless script or another app process) is heartbeating, and it isn't ours."""
    _, age = engine_heartbeat()
    eng = None if offline() else get_engine()
    ours = eng.is_running() if eng is not None else False
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
