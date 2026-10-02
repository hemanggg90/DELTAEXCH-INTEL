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
from delta_intelligence.ui.components import (LEVEL_TONE, check_results, entity_card, kpi_row, meter, page_setup,
                                              status_label, table)
from delta_intelligence.ui.format import duration, num
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("System Logs & Health", "One engine per database. Each process has its own API limiter; Delta's quota is "
                                   "shared per IP/account.")
s = get_settings()

hb, age = state.engine_heartbeat()
eng = None if state.offline() else state.get_engine()
ours = bool(eng and eng.is_running())
fresh = age is not None and age < 120
kpi_row([
    {"label": "Engine heartbeat", "value": f"{duration(age)} ago" if age is not None else "never",
     "delta": "● fresh" if fresh else "✖ stale", "tone": "good" if fresh else "critical"},
    {"label": "Engine in this app", "value": "running" if ours else "not running"},
    {"label": "Uptime (this app)", "value": duration(eng.status()["uptime_sec"]) if ours else "–"},
    {"label": "WebSocket (this app)", "value": getattr(getattr(eng, "feed", None), "status", "–") if eng else "–"},
    {"label": "Cycle errors", "value": f"{eng.cycle_errors}" if ours else "–"},
])
owner = db.get_state("engine_owner")
st.caption(f"Registered engine owner: {owner or '–'}. Restarts appear in the events below ('engine restarted').")

tab_api, tab_conn, tab_rec, tab_ev = st.tabs(["Delta API usage", "Credentials & connectivity", "Chain recorder",
                                               "System events"])
with tab_api:
    snap = LIMITER.snapshot(60)
    n429 = sum(snap["total_429"].values())
    kpi_row([
        {"label": "Weight, last 60 s", "value": f"{snap['weight_last_window']}"},
        {"label": "Weight, last 5 min", "value": f"{snap['weight_last_5min']} / {snap['quota_per_5min']}"},
        {"label": "429 responses", "value": f"{n429}", "tone": "critical" if n429 else "neutral",
         "delta": "✖ rate-limited" if n429 else None},
        {"label": "Read pause", "value": f"{snap['cooldown_remaining']:.0f}s"},
        {"label": "Network retries", "value": f"{snap.get('network_errors', 0)}"},
    ])
    used = snap["weight_last_5min"] / snap["quota_per_5min"] if snap["quota_per_5min"] else 0
    meter(used, "good" if used < .5 else "warning" if used < .8 else "critical",
          f"{used:.1%} of the 5-minute quota used by this process")
    st.caption(f"Calls in the last minute: {snap['calls_last_window']} · server quota {snap['server_quota'] or '–'}")

with tab_conn:
    entity_card("Delta credentials", [(s.delta_env, "warning" if s.delta_env == "PRODUCTION" else "neutral"),
                                      ("API key set" if s.has_credentials else "no API key",
                                       "good" if s.has_credentials else "neutral")],
                cells=[("Market data", s.data_env), ("Trading mode", "LIVE" if s.is_live_mode else "PAPER"),
                       ("PAPER needs keys", "no")])
    if st.button("Run connectivity check (time sync, public data, auth, IP whitelist)", icon=":material/network_check:"):
        from delta_intelligence.brokers.connectivity import run_connectivity_check

        with st.spinner("checking..."):
            check_results(run_connectivity_check(s, require_credentials=False))

with tab_rec:
    with db.get_session() as ses:
        n = ses.query(ChainSnapshot).count()
        last = ses.query(ChainSnapshot).order_by(ChainSnapshot.id.desc()).first()
    entity_card("Chain recorder", [("recording", "good") if last else ("no snapshots", "neutral")],
                key_value=f"{num(n, 0)} rows",
                cells=[("Latest snapshot", fmt_ist(from_db_time(last.taken_at)) if last else "–")])

with tab_ev:
    level = st.segmented_control("Levels", ["ERROR", "WARNING", "INFO"], selection_mode="multi",
                                 default=["ERROR", "WARNING"])
    with db.get_session() as ses:
        ev = ses.query(SystemEvent).filter(SystemEvent.level.in_(level or [])).order_by(
            SystemEvent.id.desc()).limit(300).all()
    table(pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(e.created_at), "%d %b %H:%M:%S"),
                         "level": status_label(LEVEL_TONE.get(e.level, "neutral"), e.level),
                         "component": e.component, "message": e.message} for e in ev]), height=420,
          empty=("No events at these levels", "Pick more levels above."))
    log = s.logs_dir / "system.log"
    if log.exists():
        with st.expander("Tail of logs/system.log"):
            st.code("".join(log.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-80:]))
