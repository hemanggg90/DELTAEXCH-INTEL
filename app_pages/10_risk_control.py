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
from delta_intelligence.ui.components import (can_control, control_note, entity_card, kpi_row, notice, page_setup,
                                              status_label, table)
from delta_intelligence.ui.format import pnl, tone, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Risk Control", "Deterministic checks with the last word. BUYING ONLY: sell-to-open is impossible at three "
                           "layers (structures, broker order guard, risk engine).")
s = get_settings()
lim = s.risk

on = kill_switch_on()
info = db.get_state(KILL_SWITCH_KEY, {}) or {}
snap = state.broker().account_snapshot()
acct = AccountTracker(s).account_state(snap, now_utc(), True, on)
eq = acct.equity
dd_pct = (acct.peak_equity - eq) / acct.peak_equity * 100 if acct.peak_equity else 0.0
kpi_row([
    {"label": "Equity", "value": usd(eq), "help": f"peak {usd(acct.peak_equity)}"},
    {"label": "Today P&L", "help": "Risk day since 00:00 IST", "value": pnl(acct.daily_pnl), "tone": tone(acct.daily_pnl)},
    {"label": "Drawdown from peak", "value": f"{dd_pct:.2f}%", "delta": f"limit {lim.max_drawdown_pct}%"},
    {"label": "Trades today", "value": f"{acct.trades_today} / {lim.max_trades_per_day}"},
    {"label": "Kill switch", "value": "✖ ON" if on else "○ off", "tone": "critical" if on else "neutral",
     "delta": "no new trades" if on else None},
])

st.subheader("Emergency kill switch")


def _ks_body() -> None:
    reason = st.text_input("Reason", "manual")
    c = st.columns(2)
    if c[0].button("ENGAGE kill switch", type="primary", disabled=not can_control() or on, width="stretch"):
        set_kill_switch(True, reason)
        st.rerun()
    if c[1].button("Release kill switch", disabled=not can_control() or not on, width="stretch"):
        set_kill_switch(False, reason)
        st.rerun()
    control_note()


entity_card("Kill switch", [("ENGAGED", "critical") if on else ("off", "good")],
            key_value=info.get("at", "")[:19].replace("T", " ") + " UTC" if on and info.get("at") else None,
            sub=(f"Reason: {info.get('reason', '')}. No new trades; exits still run." if on else
                 "Stored in the database, so it also stops an engine in another process (e.g. scripts/run_engine.py) "
                 "at its next cycle."),
            body=_ks_body)
if on:
    notice("critical", "Kill switch ENGAGED: fix the cause, check Positions & Orders, then release it here.")

st.subheader("Account vs limits")
rows = [
    ("Daily loss (since 00:00 IST)", max(0.0, -acct.daily_pnl / eq * 100), lim.max_daily_loss_pct, "%"),
    ("Drawdown from peak", dd_pct, lim.max_drawdown_pct, "%"),
    ("Trades today", acct.trades_today, lim.max_trades_per_day, ""),
    ("Open positions", acct.open_positions, lim.max_concurrent_positions, ""),
    ("Open premium, BTC+ETH bucket", acct.exposure_by_bucket.get("CRYPTO_MAJORS", 0) / eq * 100,
     lim.correlated_bucket_cap_pct, "%"),
    ("Open premium, all", acct.total_exposure / eq * 100, lim.max_portfolio_exposure_pct, "%"),
]


def _status(v: float, limit: float) -> str:
    if v >= limit:
        return status_label("critical", "AT LIMIT")
    if v >= 0.75 * limit:
        return status_label("warning", "NEAR")
    return status_label("good", "OK")


table(pd.DataFrame([{"check": n, "now": round(v, 3), "limit": l_, "unit": u, "used": min(1.0, v / l_) if l_ else 0.0,
                     "status": _status(v, l_)} for n, v, l_, u in rows]),
      {"now": st.column_config.NumberColumn(format="%.3f"), "limit": st.column_config.NumberColumn(format="%.2f"),
       "used": st.column_config.ProgressColumn("used of limit", format="percent", min_value=0.0, max_value=1.0)})

c1, c2 = st.columns([2, 3])
with c1:
    st.subheader("Configured limits")
    table(pd.DataFrame([{"limit": k, "value": str(v)} for k, v in vars(lim).items()]), height=420)
with c2:
    st.subheader("Recent risk decisions")
    with db.get_session() as ses:
        ev = ses.query(RiskEvent).order_by(RiskEvent.id.desc()).limit(200).all()
    table(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(e.created_at), "%d %b %H:%M:%S"),
                         "decision": status_label("good" if e.event_type == "APPROVED" else "critical", e.event_type)
                         if e.event_type in ("APPROVED", "VETO", "REJECTED") else e.event_type,
                         "strategy": e.strategy, "underlying": e.underlying, "reason": e.reason,
                         "decision_id": e.decision_id} for e in ev]), height=420,
          empty=("No risk decisions yet", "Every proposed trade is recorded here with its reason."))
