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
from delta_intelligence.ui.components import can_control, control_note, page_setup
from delta_intelligence.ui.format import money, pnl
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Live Trading")
s = get_settings()
rate = s.usdinr_rate

if not s.is_live_mode:
    st.info("The app is in **PAPER** mode. Live trading is off: no broker that can place real orders exists in "
            "this process.")
else:
    (st.error if s.delta_env == "PRODUCTION" else st.warning)(
        f"LIVE mode on **{s.delta_env}**. " + ("Orders on this environment use REAL funds."
                                                if s.delta_env == "PRODUCTION" else "Testnet funds only."))

st.subheader("Startup requirements")
reqs = gate_requirements(s)
st.dataframe(pd.DataFrame([{"requirement": n, "met": "yes" if ok else "NO", "detail": d} for n, ok, d in reqs]),
             hide_index=True, width="stretch")
st.caption(f"Set `TRADING_MODE=LIVE`, `TRADING_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE}`, `DELTA_ENV`, `DELTA_API_KEY` and "
           "`DELTA_API_SECRET` in `.env` or the host's secrets, then restart. Delta accepts Trading keys only from a "
           "whitelisted static IP, so run on a VPS (Streamlit Community Cloud cannot do this). The risk limits are "
           "the same as in PAPER.")

eng = state.get_engine() if (s.is_live_mode and not state.offline()) else None

st.subheader("Connectivity (read-only)")
if st.button("Run connectivity check (time sync, auth, IP whitelist, positions, orders)",
             disabled=not s.has_credentials or state.offline()):
    from delta_intelligence.brokers.connectivity import run_connectivity_check

    with st.spinner("checking..."):
        for r in run_connectivity_check(s):
            (st.warning if r.warning else st.success if r.ok else st.error)(f"{r.name}: {r.detail}")

st.subheader("Live engine")
if not s.is_live_mode or not all(ok for _, ok, _ in reqs):
    st.write("Not available until every requirement above is met.")
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
            st.error(str(exc))
            for r in exc.results:
                (st.warning if r.warning else st.success if r.ok else st.error)(f"{r.name}: {r.detail}")
    control_note()
else:
    c = st.columns([1, 1, 3])
    if eng.is_running():
        c[2].success(f"LIVE engine running since {fmt_ist(eng.started_at)} - {eng.cycles} cycles, "
                     f"{eng.cycle_errors} errors")
        if c[0].button("Stop engine", disabled=not can_control()):
            eng.stop()
            st.rerun()
    else:
        c[2].warning("LIVE engine built but stopped. Starting it reconciles with the exchange first.")
        if c[0].button("Start LIVE engine", type="primary", disabled=not can_control()):
            try:
                eng.start()
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    rec = (eng.status().get("recovery") or {}).get("reconcile")
    if rec is not None:
        (st.success if rec["ok"] else st.error)(
            "Startup reconcile: " + ("exchange and database agree." if rec["ok"] else "; ".join(rec["critical"])))
        for n in rec.get("notes", []):
            st.caption(n)
    if kill_switch_on():
        st.error("Kill switch is ENGAGED: no new trades; exits still run. Fix the cause, run reconcile, then "
                 "release it on Risk Control.")
    if st.button("Reconcile with the exchange now", disabled=not can_control()):
        r = eng.broker.reconcile()
        (st.success if r["ok"] else st.error)("OK" if r["ok"] else "; ".join(r["critical"]))
        for n in r["notes"]:
            st.caption(n)
    control_note()

    st.subheader("Live account")
    try:
        snap = eng.broker.account_snapshot()
        k = st.columns(4)
        k[0].metric("Wallet balance", money(snap["cash"], rate))
        k[1].metric("Available", money(snap["available_cash"], rate))
        k[2].metric("Open value (mid)", money(snap["open_value"], rate))
        k[3].metric("Equity", money(snap["equity"], rate))
        st.caption("Wallet field semantics (does the balance include option value?) are UNVERIFIED until confirmed "
                   "on testnet.")
    except Exception as exc:
        st.error(f"account read failed: {exc}")

st.subheader("Live book (database)")
with db.get_session() as ses:
    pos = ses.query(Position).filter(Position.mode == "LIVE").order_by(Position.id.desc()).limit(100).all()
    orders = ses.query(Order).filter(Order.mode == "LIVE").order_by(Order.id.desc()).limit(100).all()
if not pos and not orders:
    st.write("No live positions or orders recorded.")
else:
    st.dataframe(pd.DataFrame([{"opened (IST)": fmt_ist(from_db_time(p.opened_at), "%d %b %H:%M"),
                                "env": p.environment, "structure": p.structure, "underlying": p.underlying,
                                "strategy": p.strategy, "status": p.status,
                                "premium paid": p.entry_net_premium, "fees": p.fees,
                                "P&L": None if p.realized_pnl is None else pnl(p.realized_pnl),
                                "exit": p.exit_reason, "notes": p.notes} for p in pos]),
                 hide_index=True, width="stretch")
    st.caption("Orders, newest first. UNKNOWN means a send timed out and is awaiting reconcile.")
    st.dataframe(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(o.created_at), "%d %b %H:%M:%S"),
                                "symbol": o.symbol, "side": o.side, "purpose": o.purpose, "size": o.size,
                                "limit": o.limit_price, "reduce_only": o.reduce_only, "status": o.status,
                                "note": o.reject_reason, "client_order_id": o.client_order_id} for o in orders]),
                 hide_index=True, width="stretch")

st.markdown("""
**Safety, regardless of mode:** buying options only (sell-to-open is rejected by the structures, the broker order
guard and the risk engine); sells are reduce-only closes; orders are never auto-retried (a timeout is resolved through
its `client_order_id`, and otherwise engages the kill switch); there is no withdrawal function; the dead-man's-switch
heartbeat is off because it would also cancel protective orders.
""")
