"""Risk Control: emergency kill switch, account state vs limits, configured limits, recent risk events."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import RiskEvent, from_db_time
from delta_intelligence.execution.account import AccountTracker
from delta_intelligence.execution.engine import KILL_SWITCH_KEY, kill_switch_on, set_kill_switch
from delta_intelligence.ui import state
from delta_intelligence.ui.components import can_control, control_note, page_setup
from delta_intelligence.ui.format import money, pct
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Risk Control")
s = get_settings()
lim = s.risk

st.subheader("Emergency kill switch")
on = kill_switch_on()
info = db.get_state(KILL_SWITCH_KEY, {}) or {}
if on:
    st.error(f"KILL SWITCH ENGAGED since {info.get('at', '?')} - {info.get('reason', '')}. No new trades; exits still run.")
else:
    st.success("Kill switch is off.")
reason = st.text_input("Reason", "manual")
c = st.columns(2)
if c[0].button("ENGAGE kill switch", type="primary", disabled=not can_control() or on):
    set_kill_switch(True, reason)
    st.rerun()
if c[1].button("Release kill switch", disabled=not can_control() or not on):
    set_kill_switch(False, reason)
    st.rerun()
control_note()
st.caption("The kill switch is stored in the database, so it also stops an engine running in another process "
           "(e.g. scripts/run_engine.py) at its next cycle.")

st.subheader("Account vs limits")
snap = state.broker().account_snapshot()
acct = AccountTracker(s).account_state(snap, now_utc(), True, on)
eq = acct.equity
rows = [
    ("Daily loss (since 00:00 IST)", -acct.daily_pnl / eq * 100, lim.max_daily_loss_pct),
    ("Drawdown from peak", (acct.peak_equity - eq) / acct.peak_equity * 100, lim.max_drawdown_pct),
    ("Trades today", acct.trades_today, lim.max_trades_per_day),
    ("Open positions", acct.open_positions, lim.max_concurrent_positions),
    ("Open premium, BTC+ETH bucket (%)", acct.exposure_by_bucket.get("CRYPTO_MAJORS", 0) / eq * 100, lim.correlated_bucket_cap_pct),
    ("Open premium, all (%)", acct.total_exposure / eq * 100, lim.max_portfolio_exposure_pct),
]
st.dataframe(pd.DataFrame([{"check": n, "now": round(v, 3), "limit": l_, "status": "OK" if v < l_ else "AT LIMIT"}
                           for n, v, l_ in rows]), hide_index=True, width="stretch")
st.caption(f"Equity {money(eq, s.usdinr_rate)} · peak {money(acct.peak_equity, s.usdinr_rate)}")

st.subheader("Configured limits (BUYING ONLY - sell-to-open is impossible at three layers)")
st.dataframe(pd.DataFrame([{"limit": k, "value": v} for k, v in vars(lim).items()]), hide_index=True, width="stretch")

st.subheader("Recent risk decisions")
with db.get_session() as ses:
    ev = ses.query(RiskEvent).order_by(RiskEvent.id.desc()).limit(200).all()
st.dataframe(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(e.created_at), "%d %b %H:%M:%S"), "decision": e.event_type,
                            "strategy": e.strategy, "underlying": e.underlying, "reason": e.reason,
                            "decision_id": e.decision_id} for e in ev]), hide_index=True, width="stretch")
