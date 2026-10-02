"""Command Center: account, engine control, latest decision per underlying, active strategies."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Decision, from_db_time
from delta_intelligence.execution.account import AccountTracker
from delta_intelligence.execution.engine import kill_switch_on
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.ui import state
from delta_intelligence.ui.components import (DECISION_TONE, can_control, control_note, empty_state, entity_card,
                                              kpi_row, meter, notice, page_setup, status_label, table)
from delta_intelligence.ui.format import duration, money, num, pnl, tone, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Command Center", "Research software, not financial advice. The backtests found no proven edge (see "
                             "Research Reports). PAPER by default; the active strategies are a paper experiment.")
s = get_settings()
rate = s.usdinr_rate


@st.fragment(run_every=15)
def account_tiles() -> None:
    b = state.broker()
    snap = b.account_snapshot()
    mode = getattr(b, "mode", "PAPER")
    live = state.live_mode() and state.get_engine() is not None
    tracker = AccountTracker(s)
    day_start = (tracker._state(mode) or {}).get("day_start_equity")
    day_pnl = None if day_start is None else snap["equity"] - float(day_start)
    vs_start = snap["equity"] - s.paper_starting_capital_usd
    ks = kill_switch_on()
    kpi_row([
        {"label": "LIVE equity" if live else "Paper equity", "value": usd(snap["equity"]),
         "delta": None if live else pnl(vs_start) + " vs start", "tone": tone(vs_start),
         "help": money(snap["equity"], rate)},
        {"label": "Today P&L", "help": "Risk day since 00:00 IST", "value": pnl(day_pnl), "tone": tone(day_pnl)},
        {"label": "Open positions", "value": f"{snap['open_positions']} / {s.risk.max_concurrent_positions}"},
        {"label": "Premium at risk", "value": usd(snap["total_exposure"]),
         "delta": f"{snap['total_exposure'] / snap['equity'] * 100:.2f}% of equity" if snap["equity"] else None},
        {"label": "Trades today", "value": f"{tracker.trades_today(now_utc(), mode)} / {s.risk.max_trades_per_day}"},
        {"label": "Kill switch", "value": "✖ ON" if ks else "○ off", "delta": "no new trades" if ks else None,
         "tone": "critical" if ks else "neutral"},
    ])


account_tiles()

left, right = st.columns([3, 2])
with left:
    st.subheader("Engine")
    hb, age = state.engine_heartbeat()
    external = (not state.offline()) and state.external_engine_running()
    eng = None if state.offline() else state.get_engine()
    with st.container(border=True):
        if eng is not None and eng.is_running():
            c = st.columns([4, 1], vertical_alignment="center")
            with c[0]:
                notice("good", f"Running in this app since {fmt_ist(eng.started_at)} · {eng.cycles} cycles · "
                               f"{eng.cycle_errors} errors")
            if c[1].button("Stop engine", disabled=not can_control(), width="stretch"):
                eng.stop()
                st.rerun()
        elif external:
            notice("info", f"An engine is running in another process (heartbeat {duration(age)} ago), e.g. "
                           "`scripts/run_engine.py`. Start is disabled here: two engines would double-trade. Use the "
                           "kill switch (Risk Control) to block new trades, or stop that process.")
        else:
            c = st.columns([4, 1], vertical_alignment="center")
            with c[0]:
                notice("warning", "Engine stopped." + (f" Last heartbeat {fmt_ist(hb)}." if hb else "")
                       + " Start it here, or run `python scripts/run_engine.py` for a headless 24x7 engine.")
            if c[1].button("Start engine", type="primary", disabled=not can_control() or eng is None, width="stretch"):
                try:
                    eng.start()
                    st.rerun()
                except RuntimeError as exc:
                    notice("critical", str(exc))
        if age is not None:
            meter(max(0.0, 1 - age / 120), "good" if age < 120 else "critical",
                  f"heartbeat {duration(age)} ago (stale after 2m)")
        control_note()
with right:
    st.subheader("Active strategies (paper)")
    with st.container(border=True):
        for v in ACTIVE_VARIANTS:
            st.markdown(f"**{v.key}**")
        st.caption("Chosen in the strategy search; weak evidence (Research Reports → STRATEGY_SEARCH).")

st.subheader("Latest decision per underlying")
with db.get_session() as ses:
    rows = ses.query(Decision).order_by(Decision.id.desc()).limit(60).all()
latest: dict = {}
for d in rows:
    latest.setdefault(d.underlying, d)
if not latest:
    empty_state("No decisions yet", "Start the engine: it scans once per closed 5m bar.")
cols = st.columns(max(1, min(3, len(latest))))
for i, (u, d) in enumerate(latest.items()):
    detail = (d.ranking or {}).get("detail") or d.no_trade_reason or ""
    with cols[i % len(cols)]:
        entity_card(u, [(d.setup_status or "–", DECISION_TONE.get(d.setup_status or "", "neutral")),
                        (f"data {d.data_quality}", "good" if d.data_quality in ("OK", "PASS") else "warning")],
                    key_value=fmt_ist(from_db_time(d.bar_time), "%H:%M IST"),
                    cells=[("IV percentile", num((d.ranking or {}).get("iv_percentile"), 0))]
                    + [(p.split(":")[0][:28], p.split(":", 1)[1].strip()) if ":" in p else ("note", p)
                       for p in detail.split("; ") if p][:8])

st.subheader("Decision history")
hist = pd.DataFrame([{"time (IST)": fmt_ist(from_db_time(d.bar_time), "%d %b %H:%M"), "underlying": d.underlying,
                      "status": status_label(DECISION_TONE.get(d.setup_status or "", "neutral"), d.setup_status or "–"), "detail": (d.ranking or {}).get("detail") or d.no_trade_reason}
                     for d in rows])
table(hist, height=320, empty=("No decisions yet", "Decisions appear once the engine runs."))
