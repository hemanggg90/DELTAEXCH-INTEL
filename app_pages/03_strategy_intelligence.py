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
from delta_intelligence.ui.components import page_setup
from delta_intelligence.ui.research import strategy_frame

page_setup("Strategy Intelligence")
s = get_settings()
st.warning("No strategy passed the acceptance rules (>= 200 trades, positive out-of-sample net R, stable under ±20% "
           "parameter changes, positive on >= 2 underlyings). The active paper strategies come from a search with weak "
           "evidence. Treat everything here as research.")

p = s.data_cache_dir / "research_report_v3.json"
if p.exists():
    rep = json.loads(p.read_text(encoding="utf-8"))
    rows = [{"strategy": n, "verdict": "ACCEPTED" if v["accepted"] else "rejected", "trades": v["n_trades"],
             "pooled net R": v["pooled_net_r"], "OOS net R": v["oos_net_r"],
             "positive on": ", ".join(v["positive_underlyings"]), "reasons": "; ".join(v["reasons"])}
            for n, v in rep["verdicts"].items()]
    st.subheader("v3 acceptance verdicts")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
else:
    st.info("Run scripts/research_report_v3.py to populate verdicts.")

st.subheader("Setup state on the latest closed bar")
perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
f = strategy_frame(perp, 75)
if f is None:
    st.info("No data.")
    st.stop()
rows = []
active = {v.key: v for v in ACTIVE_VARIANTS}
for strat in get_all_strategies() + [v.strategy() for v in ACTIVE_VARIANTS]:
    setup = strat.setup_now(f)
    hist = strat.historical_setups(f)
    rows.append({"strategy": strat.name, "active (paper)": strat.name in active,
                 "setup now": f"{setup.direction} stop {setup.stop_price:.2f} target {setup.target_price:.2f}" if setup else "-",
                 "setups in window": len(hist), "policy": strat.policy,
                 "expected hold (h)": strat.expected_hold_bars * 5 / 60})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
