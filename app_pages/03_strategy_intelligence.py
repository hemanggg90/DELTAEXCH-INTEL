"""Strategy Intelligence: research verdicts per strategy (v3 acceptance report, strategy search), plus the current
setup state of every strategy on the latest bar."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import get_all_strategies
from delta_intelligence.ui.ranker_view import ranker_note
from delta_intelligence.ui.components import empty_state, kpi_row, notice, page_setup, status_label, table
from delta_intelligence.ui.research import strategy_frame

page_setup("Strategy Intelligence", "Acceptance: ≥ 200 trades, positive out-of-sample net R, stable under ±20% "
                                    "parameter changes, positive on ≥ 2 underlyings.")
s = get_settings()
notice("warning", "No strategy passed the acceptance rules. The active paper strategies come from a search with weak "
                  "evidence. Treat everything here as research.")

R = st.column_config.NumberColumn(format="%+.3f")
tab_v, tab_s, tab_r = st.tabs(["v3 acceptance verdicts", "Setup state now", "Live ranker"])
with tab_v:
    p = s.data_cache_dir / "research_report_v3.json"
    if p.exists():
        rep = json.loads(p.read_text(encoding="utf-8"))
        verdicts = rep["verdicts"]
        n_acc = sum(1 for v in verdicts.values() if v["accepted"])
        kpi_row([{"label": "Strategies tested", "value": f"{len(verdicts)}"},
                 {"label": "Accepted", "value": f"{n_acc}", "tone": "good" if n_acc else "critical",
                  "delta": "● some passed" if n_acc else "✖ none passed"},
                 {"label": "Trades (all)", "value": f"{sum(v['n_trades'] for v in verdicts.values()):,}"}])
        rows = [{"strategy": n, "verdict": status_label("good", "ACCEPTED") if v["accepted"] else
                 status_label("critical", "rejected"), "trades": v["n_trades"],
                 "pooled net R": v["pooled_net_r"], "OOS net R": v["oos_net_r"],
                 "positive on": ", ".join(v["positive_underlyings"]), "reasons": "; ".join(v["reasons"])}
                for n, v in verdicts.items()]
        table(pd.DataFrame(rows), {"pooled net R": R, "OOS net R": R})
    else:
        empty_state("No verdicts yet", "Run scripts/research_report_v3.py to populate them.")

with tab_r:
    from delta_intelligence.database import db
    from delta_intelligence.database.models import Decision

    with db.get_session() as _ses:
        _rows = _ses.query(Decision).order_by(Decision.id.desc()).limit(60).all()
    _latest: dict = {}
    for _d in _rows:
        _latest.setdefault(_d.underlying, _d)
    if not _latest:
        empty_state("No decisions yet", "Start the engine (Command Center): the ranker runs once per closed 5m bar.")
    for _u, _d in _latest.items():
        st.markdown(f"**{_u}**")
        ranker_note(_d)

with tab_s:
    perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
    f = strategy_frame(perp, 75)
    if f is None:
        empty_state("No data", "Run scripts/fetch_candles.py to fill the candle cache.")
        st.stop()
    rows = []
    active = {v.key: v for v in ACTIVE_VARIANTS}
    for strat in get_all_strategies() + [v.strategy() for v in ACTIVE_VARIANTS]:
        setup = strat.setup_now(f)
        hist = strat.historical_setups(f)
        rows.append({"strategy": strat.name, "active (paper)": strat.name in active,
                     "setup now": (status_label("info", f"{setup.direction} stop {setup.stop_price:.2f} target "
                                                        f"{setup.target_price:.2f}") if setup else
                                   status_label("neutral", "none")),
                     "setups in window": len(hist), "policy": strat.policy,
                     "expected hold (h)": strat.expected_hold_bars * 5 / 60})
    table(pd.DataFrame(rows), {"expected hold (h)": st.column_config.NumberColumn(format="%.1f"),
                               "active (paper)": st.column_config.CheckboxColumn()})
