"""
delta-intelligence dashboard: the single Streamlit entry point.

    streamlit run app.py

An OPTIONAL password gate (APP_PASSWORD from .env / Streamlit secrets) sits in front of every page when set. With no
password, PAPER mode is open with full controls; LIVE mode without a password is read-only. Pages live in app_pages/
(not pages/), so Streamlit's legacy auto-discovery can't bypass the gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from delta_intelligence.ui.components import gate  # noqa: E402

st.set_page_config(page_title="delta-intelligence", page_icon="📈", layout="wide")

P = "app_pages/"
PAGES = {
    "Trading": [
        st.Page(P + "01_command_center.py", title="Command Center", icon="🧭", default=True),
        st.Page(P + "08_paper_trading.py", title="Paper Trading", icon="📝"),
        st.Page(P + "09_positions_orders.py", title="Positions & Orders", icon="📂"),
        st.Page(P + "10_risk_control.py", title="Risk Control", icon="🛑"),
        st.Page(P + "13_live_trading.py", title="Live Trading", icon="⚠️"),
    ],
    "Research": [
        st.Page(P + "02_market_state.py", title="Market State", icon="🌡️"),
        st.Page(P + "03_strategy_intelligence.py", title="Strategy Intelligence", icon="🧠"),
        st.Page(P + "04_strategy_library.py", title="Strategy Library", icon="📚"),
        st.Page(P + "05_backtest_lab.py", title="Backtest Lab", icon="🧪"),
        st.Page(P + "06_regime_analysis.py", title="Regime Analysis", icon="🌀"),
        st.Page(P + "07_historical_analogues.py", title="Historical Analogues", icon="🔎"),
        st.Page(P + "11_research_reports.py", title="Research Reports", icon="📄"),
    ],
    "System": [st.Page(P + "12_system_health.py", title="System Logs & Health", icon="🩺")],
}

if gate():
    st.navigation(PAGES).run()
