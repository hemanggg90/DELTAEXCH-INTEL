"""Shared page furniture: theme CSS, the status bar shown on every page, KPI tiles and the password gate."""
from __future__ import annotations

import hmac

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.ui import state
from delta_intelligence.utils.timeutil import fmt_ist, liquidity_sessions_at, next_funding_time, now_utc, parse_sessions

CSS = """
<style>
.di-bar {display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;padding:.45rem .8rem;border-radius:8px;
         background:var(--secondary-background-color);font-size:.86rem;margin-bottom:.6rem}
.di-bar b {font-weight:600}
.di-ok {color:#1a7f37} .di-bad {color:#c62828} .di-warn {color:#b26a00}
.di-pill {padding:0 .45rem;border-radius:999px;border:1px solid currentColor;font-size:.78rem}
</style>
"""


def page_setup(title: str) -> None:
    """Call first on every page."""
    state.ensure_db()
    st.markdown(CSS, unsafe_allow_html=True)
    status_bar()
    st.title(title)


def _next_expiry(asset: str):
    from delta_intelligence.config.watchlist import SETTLE_HOUR_BY_ASSET

    now = pd.Timestamp(now_utc())
    hour = SETTLE_HOUR_BY_ASSET.get(asset, 12)
    exp = now.normalize() + pd.Timedelta(hours=hour)
    return exp if exp > now else exp + pd.Timedelta(days=1)


@st.fragment(run_every=10)
def status_bar() -> None:
    s = get_settings()
    now = now_utc()
    sessions = ", ".join(liquidity_sessions_at(now, parse_sessions(s.clocks.liquidity_sessions))) or "off-session"
    hb, age = state.engine_heartbeat()
    eng_ok = age is not None and age < 120
    from delta_intelligence.execution.engine import kill_switch_on

    ks = kill_switch_on()
    btc_exp = _next_expiry("BTC")
    x_exp = _next_expiry("XAUT")
    left = lambda e: f"{(e - pd.Timestamp(now)).total_seconds() / 3600:.1f} h"  # noqa: E731
    mode = "LIVE" if s.is_live_mode else "PAPER"
    parts = [
        f"🕒 <b>{fmt_ist(now, '%d %b %H:%M:%S IST')}</b>",
        f"Session: <b>{sessions}</b>",
        f"Next funding: <b>{fmt_ist(next_funding_time(now), '%H:%M IST')}</b>",
        f"BTC/ETH expiry: <b>{fmt_ist(btc_exp, '%d %b %H:%M IST')}</b> ({left(btc_exp)})",
        f"XAUT expiry: <b>{fmt_ist(x_exp, '%H:%M IST')}</b> ({left(x_exp)})",
        f"Engine: <span class='{'di-ok' if eng_ok else 'di-bad'}'><b>{'running' if eng_ok else 'stopped'}</b></span>"
        + (f" (heartbeat {age:.0f}s ago)" if age is not None else ""),
        f"<span class='di-pill {'di-bad' if s.is_live_mode else 'di-ok'}'>{mode} · data {s.data_env}</span>",
        f"<span class='di-pill {'di-bad' if ks else 'di-ok'}'>kill switch {'ON' if ks else 'off'}</span>",
    ]
    st.markdown(f"<div class='di-bar'>{' · '.join(parts)}</div>", unsafe_allow_html=True)


def kpi(cols, label: str, value: str, help_text: str | None = None) -> None:
    cols.metric(label, value, help=help_text)


# ---- access control ----------------------------------------------------------------------------------------------
def password_configured() -> bool:
    return bool(get_settings().app_password)


def authenticated() -> bool:
    return bool(st.session_state.get("_authed"))


def open_access() -> bool:
    """No password configured AND not in LIVE mode: the dashboard runs with full controls and no login. This is for
    PAPER trading only. LIVE mode never runs open: it needs APP_PASSWORD."""
    return not password_configured() and not get_settings().is_live_mode


def can_control() -> bool:
    """Controls (kill switch, engine start/stop, orders, exits). With a password: a successful login. Without one:
    allowed in PAPER mode only (`open_access`), never in LIVE mode."""
    if password_configured():
        return authenticated()
    return open_access()


def gate() -> bool:
    """Optional password gate. With APP_PASSWORD set, every page needs a login. Without it, PAPER mode is open (full
    controls); LIVE mode without a password renders READ-ONLY."""
    if not password_configured():
        if get_settings().is_live_mode:
            st.sidebar.warning("LIVE mode needs APP_PASSWORD: with none set the dashboard is READ-ONLY (no engine "
                               "control, kill switch or orders).")
        return True
    if authenticated():
        if st.sidebar.button("Log out"):
            st.session_state["_authed"] = False
            st.rerun()
        return True
    st.title("delta-intelligence")
    pw = st.text_input("Password", type="password")
    if st.button("Log in"):
        if hmac.compare_digest(pw.encode(), get_settings().app_password.encode()):
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


def control_note() -> None:
    if not can_control():
        st.info("Controls are disabled: " + ("log in to use them." if password_configured()
                                             else "LIVE mode needs APP_PASSWORD (PAPER mode does not)."))
