"""System Logs & Health: engine heartbeat/uptime, WebSocket, API usage per minute and 429s, credentials/IP status,
chain recorder and recent system events."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.brokers.rate_limit import LIMITER
from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot, SystemEvent, from_db_time
from delta_intelligence.ui import state
from delta_intelligence.ui.components import page_setup
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("System Logs & Health")
s = get_settings()

hb, age = state.engine_heartbeat()
c = st.columns(4)
c[0].metric("Engine heartbeat", f"{age:.0f}s ago" if age is not None else "never")
eng = None if state.offline() else state.get_engine()
c[1].metric("Engine in this app", "running" if eng and eng.is_running() else "not running")
c[2].metric("Uptime (this app's engine)", f"{eng.status()['uptime_sec'] / 3600:.1f} h" if eng and eng.is_running() else "-")
c[3].metric("WebSocket (this app)", getattr(getattr(eng, "feed", None), "status", "-") if eng else "-")
owner = db.get_state("engine_owner")
st.caption(f"Registered engine owner: {owner}. Restart events are listed below ('engine restarted').")

st.subheader("Delta API usage (this process)")
snap = LIMITER.snapshot(60)
c = st.columns(5)
c[0].metric("Weight, last 60 s", snap["weight_last_window"])
c[1].metric("Weight, last 5 min", f"{snap['weight_last_5min']} / {snap['quota_per_5min']}")
c[2].metric("429 responses", sum(snap["total_429"].values()))
c[3].metric("Read pause", f"{snap['cooldown_remaining']:.0f}s")
c[4].metric("Network retries", snap.get("network_errors", 0))
st.caption(f"Calls in the last minute: {snap['calls_last_window']} · server quota {snap['server_quota']}. Each process "
           "(app, headless engine) has its own limiter; Delta's quota is shared per IP/account, so run one engine.")

st.subheader("Credentials and connectivity")
st.write(f"Environment `{s.delta_env}` · API key {'set' if s.has_credentials else 'NOT set'} "
         f"(PAPER needs none) · market data from `{s.data_env}`")
if st.button("Run connectivity check (time sync, public data, auth, IP whitelist)"):
    from delta_intelligence.brokers.connectivity import run_connectivity_check

    with st.spinner("checking..."):
        for r in run_connectivity_check(s, require_credentials=False):
            (st.warning if r.warning else st.success if r.ok else st.error)(f"{r.name}: {r.detail}")

st.subheader("Chain recorder")
with db.get_session() as ses:
    n = ses.query(ChainSnapshot).count()
    last = ses.query(ChainSnapshot).order_by(ChainSnapshot.id.desc()).first()
st.write(f"{n:,} option rows recorded" + (f"; latest at {fmt_ist(from_db_time(last.taken_at))}" if last else ""))

st.subheader("Recent system events")
level = st.multiselect("Levels", ["ERROR", "WARNING", "INFO"], default=["ERROR", "WARNING"])
with db.get_session() as ses:
    ev = ses.query(SystemEvent).filter(SystemEvent.level.in_(level)).order_by(SystemEvent.id.desc()).limit(300).all()
st.dataframe(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(e.created_at), "%d %b %H:%M:%S"), "level": e.level,
                            "component": e.component, "message": e.message} for e in ev]),
             hide_index=True, width="stretch")
log = s.logs_dir / "system.log"
if log.exists():
    with st.expander("Tail of logs/system.log"):
        st.code("".join(log.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-80:]))
