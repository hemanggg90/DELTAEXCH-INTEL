"""Command Center: account, engine control, latest decision per underlying, active strategies."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Decision, from_db_time
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.ui import state
from delta_intelligence.ui.components import can_control, control_note, page_setup
from delta_intelligence.ui.format import money, pnl
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Command Center")
s = get_settings()
rate = s.usdinr_rate

st.caption("Research software, not financial advice. The backtests found **no proven edge** "
           "(see Research Reports). Trading is PAPER by default; the active strategies are a paper experiment.")


@st.fragment(run_every=15)
def account_tiles() -> None:
    snap = state.broker().account_snapshot()
    start = s.paper_starting_capital_usd
    c = st.columns(4)
    c[0].metric("Paper equity", money(snap["equity"], rate))
    c[1].metric("P&L since start", pnl(snap["equity"] - start))
    c[2].metric("Open positions", f"{snap['open_positions']}")
    c[3].metric("Premium at risk", money(snap["total_exposure"], rate))


account_tiles()

st.subheader("Engine")
hb, age = state.engine_heartbeat()
external = (not state.offline()) and state.external_engine_running()
eng = None if state.offline() else state.get_engine()
cols = st.columns([1, 1, 3])
if eng is not None and eng.is_running():
    cols[2].success(f"Running in this app since {fmt_ist(eng.started_at)} - {eng.cycles} cycles, "
                    f"{eng.cycle_errors} errors")
    if cols[0].button("Stop engine", disabled=not can_control()):
        eng.stop()
        st.rerun()
elif external:
    cols[2].info(f"An engine is already running in another process (heartbeat {age:.0f}s ago), e.g. "
                 "`scripts/run_engine.py`. Start is disabled: two engines would double-trade. Use the kill switch "
                 "(Risk Control) to block new trades, or stop that process.")
else:
    cols[2].warning("Engine stopped." + (f" Last heartbeat {fmt_ist(hb)}." if hb else ""))
    if cols[0].button("Start engine", disabled=not can_control() or eng is None):
        try:
            eng.start()
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))
control_note()

st.subheader("Active strategies (paper)")
st.write(", ".join(f"**{v.key}**" for v in ACTIVE_VARIANTS) +
         ". Chosen in the strategy search; weak evidence (see Research Reports → STRATEGY_SEARCH).")

st.subheader("Latest decision per underlying")
with db.get_session() as ses:
    rows = ses.query(Decision).order_by(Decision.id.desc()).limit(60).all()
latest = {}
for d in rows:
    latest.setdefault(d.underlying, d)
if not latest:
    st.write("No decisions yet: start the engine.")
for u, d in latest.items():
    with st.container(border=True):
        st.markdown(f"**{u}** · bar {fmt_ist(from_db_time(d.bar_time), '%d %b %H:%M IST')} · status "
                    f"**{d.setup_status}** · data {d.data_quality}")
        detail = (d.ranking or {}).get("detail") or d.no_trade_reason or ""
        for part in detail.split("; "):
            st.write("•", part)

with st.expander("Decision history"):
    hist = pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(d.bar_time), "%d %b %H:%M"), "underlying": d.underlying,
                          "status": d.setup_status, "detail": (d.ranking or {}).get("detail")} for d in rows])
    st.dataframe(hist, hide_index=True, width="stretch")
