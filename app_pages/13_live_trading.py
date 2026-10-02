"""Live Trading: the LIVE startup gate, the live engine, reconciliation and the live book.

Nothing here can place an order by itself: orders come only from the engine (strategy -> planner -> risk engine ->
DeltaBroker). The engine is built only after the double gate AND a passing read-only connectivity check, and it
reconciles against the exchange before its first trade. Buying only; sells are reduce-only closes.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.brokers.live_gate import LiveGateError, gate_requirements
from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Order, Position, from_db_time
from delta_intelligence.execution.engine import kill_switch_on
from delta_intelligence.ui import state
from delta_intelligence.ui.components import (can_control, check_results, control_note, empty_state, kpi_row, notice,
                                              page_setup, status_label, table)
from delta_intelligence.ui.format import pnl, usd
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Live Trading", "Orders come only from the engine, through the risk engine, to DeltaBroker. Buying only; "
                           "sells are reduce-only closes.")
s = get_settings()

if not s.is_live_mode:
    notice("info", "The app is in PAPER mode. Live trading is off: no broker that can place real orders exists in "
                   "this process.")
elif s.delta_env == "PRODUCTION":
    notice("critical", f"LIVE mode on {s.delta_env}. Orders on this environment use REAL funds.")
else:
    notice("warning", f"LIVE mode on {s.delta_env}. Testnet funds only.")

reqs = gate_requirements(s)
n_ok = sum(1 for _, ok, _ in reqs if ok)
eng = state.get_engine() if (s.is_live_mode and not state.offline()) else None
kpi_row([
    {"label": "Mode", "value": "LIVE" if s.is_live_mode else "PAPER",
     "tone": "critical" if s.is_live_mode and s.delta_env == "PRODUCTION" else "neutral"},
    {"label": "Environment", "value": s.delta_env},
    {"label": "Startup requirements", "value": f"{n_ok} / {len(reqs)}",
     "delta": "● all met" if n_ok == len(reqs) else "✖ not met", "tone": "good" if n_ok == len(reqs) else "critical"},
    {"label": "Live engine", "value": "running" if eng and eng.is_running() else "built" if eng else "not built"},
])

st.subheader("Startup requirements")
table(pd.DataFrame([{"requirement": n, "met": status_label("good", "yes") if ok else status_label("critical", "NO"),
                     "detail": d} for n, ok, d in reqs]))
st.caption(f"Set `TRADING_MODE=LIVE`, `TRADING_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE}`, `DELTA_ENV`, `DELTA_API_KEY` and "
           "`DELTA_API_SECRET` in `.env` or the host's secrets, then restart. Delta accepts Trading keys only from a "
           "whitelisted static IP, so run on a VPS (Streamlit Community Cloud cannot do this). The risk limits are "
           "the same as in PAPER.")

st.subheader("Connectivity (read-only)")
if st.button("Run connectivity check (time sync, auth, IP whitelist, positions, orders)",
             disabled=not s.has_credentials or state.offline(), icon=":material/network_check:"):
    from delta_intelligence.brokers.connectivity import run_connectivity_check

    with st.spinner("checking..."):
        check_results(run_connectivity_check(s))

st.subheader("Live engine")
with st.container(border=True):
    if not s.is_live_mode or not all(ok for _, ok, _ in reqs):
        empty_state("Not available", "Every startup requirement above must be met first.")
    elif eng is None:
        st.write("The live engine has not been built in this process. Building it runs the startup gate.")
        ack = st.checkbox(f"I understand this will place real orders on {s.delta_env} when strategies trigger.")
        typed = st.text_input(f"Type the environment name ({s.delta_env}) to confirm")
        if st.button("Run startup gate and build live engine", type="primary",
                     disabled=not (can_control() and ack and typed.strip().upper() == s.delta_env) or state.offline()):
            try:
                with st.spinner("running startup gate..."):
                    state.build_live_engine()
                st.rerun()
            except LiveGateError as exc:
                notice("critical", f"{exc} Fix the failing checks below, then try again.")
                check_results(exc.results)
        control_note()
    else:
        c = st.columns([4, 1], vertical_alignment="center")
        if eng.is_running():
            with c[0]:
                notice("good", f"LIVE engine running since {fmt_ist(eng.started_at)} · {eng.cycles} cycles · "
                               f"{eng.cycle_errors} errors")
            if c[1].button("Stop engine", disabled=not can_control(), width="stretch"):
                eng.stop()
                st.rerun()
        else:
            with c[0]:
                notice("warning", "LIVE engine built but stopped. Starting it reconciles with the exchange first.")
            if c[1].button("Start LIVE engine", type="primary", disabled=not can_control(), width="stretch"):
                try:
                    eng.start()
                    st.rerun()
                except RuntimeError as exc:
                    notice("critical", str(exc))
        rec = (eng.status().get("recovery") or {}).get("reconcile")
        if rec is not None:
            notice("good" if rec["ok"] else "critical",
                   "Startup reconcile: " + ("exchange and database agree." if rec["ok"] else "; ".join(rec["critical"])))
            for n in rec.get("notes", []):
                st.caption(n)
        if kill_switch_on():
            notice("critical", "Kill switch is ENGAGED: no new trades; exits still run. Fix the cause, run reconcile, "
                               "then release it on Risk Control.")
        if st.button("Reconcile with the exchange now", disabled=not can_control()):
            r = eng.broker.reconcile()
            notice("good" if r["ok"] else "critical", "OK" if r["ok"] else "; ".join(r["critical"]))
            for n in r["notes"]:
                st.caption(n)
        control_note()

if eng is not None:
    st.subheader("Live account")
    try:
        snap = eng.broker.account_snapshot()
        kpi_row([{"label": "Wallet balance", "value": usd(snap["cash"])},
                 {"label": "Available", "value": usd(snap["available_cash"])},
                 {"label": "Open value (mid)", "value": usd(snap["open_value"])},
                 {"label": "Equity", "value": usd(snap["equity"])}])
        st.caption("Wallet field semantics (does the balance include option value?) are UNVERIFIED until confirmed "
                   "on testnet.")
    except Exception as exc:
        notice("critical", f"Account read failed: {exc}. Run the connectivity check above.")

st.subheader("Live book (database)")
with db.get_session() as ses:
    pos = ses.query(Position).filter(Position.mode == "LIVE").order_by(Position.id.desc()).limit(100).all()
    orders = ses.query(Order).filter(Order.mode == "LIVE").order_by(Order.id.desc()).limit(100).all()
USD = st.column_config.NumberColumn(format="$%.2f")
tab_p, tab_o = st.tabs(["Positions", "Orders"])
with tab_p:
    table(pd.DataFrame([{"opened (IST)": fmt_ist(from_db_time(p.opened_at), "%d %b %H:%M"),
                         "env": p.environment, "structure": p.structure, "underlying": p.underlying,
                         "strategy": p.strategy, "status": p.status, "premium paid": p.entry_net_premium,
                         "fees": p.fees, "P&L": pnl(p.realized_pnl), "exit": p.exit_reason, "notes": p.notes}
                        for p in pos]), {"premium paid": USD, "fees": USD},
          empty=("No live positions recorded", "Live positions appear once the live engine trades."))
with tab_o:
    st.caption("Newest first. UNKNOWN means a send timed out and is awaiting reconcile.")
    table(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(o.created_at), "%d %b %H:%M:%S"),
                         "symbol": o.symbol, "side": o.side, "purpose": o.purpose, "size": o.size,
                         "limit": o.limit_price, "reduce_only": o.reduce_only, "status": o.status,
                         "note": o.reject_reason, "client_order_id": o.client_order_id} for o in orders]),
          empty=("No live orders recorded", ""))

with st.expander("Safety, regardless of mode", icon=":material/shield:"):
    st.markdown("Buying options only (sell-to-open is rejected by the structures, the broker order guard and the risk "
                "engine); sells are reduce-only closes; orders are never auto-retried (a timeout is resolved through "
                "its `client_order_id`, and otherwise engages the kill switch); there is no withdrawal function; the "
                "dead-man's-switch heartbeat is off because it would also cancel protective orders.")
