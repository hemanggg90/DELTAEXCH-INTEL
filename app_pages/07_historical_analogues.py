"""Historical Analogues: the past setups most similar to now, for a chosen strategy, with their option outcomes."""
from __future__ import annotations

import streamlit as st

from delta_intelligence.analogues.analogue_engine import conditional_metrics_from_analogues, find_analogues
from delta_intelligence.backtesting.option_backtest import run_option_backtest
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.research.ranking_core import observations_from_trades
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import get_all_strategies
from delta_intelligence.ui.components import empty_state, kpi_row, notice, page_setup, table
from delta_intelligence.ui.format import pct, r_mult, tone
from delta_intelligence.ui.research import features, market, strategy_frame

page_setup("Historical Analogues", "Analogues from the same day are correlated, so confidence counts independent days, "
                                   "not rows.")
strats = {s.name: s for s in [v.strategy() for v in ACTIVE_VARIANTS] + get_all_strategies()}
with st.container(border=True):
    c = st.columns(3)
    name = c[0].selectbox("Strategy", list(strats))
    perp = c[1].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
    k = c[2].slider("Analogues (k)", 10, 60, 30)
    go = st.button("Find analogues", type="primary", icon=":material/manage_search:")
if not go:
    empty_state("No search run yet", "Pick a strategy and underlying, then press Find analogues.")
    st.stop()
days = 120
f = strategy_frame(perp, days)
mkt, _ = market(UNDERLYINGS[perp].asset, perp, days)
_, feats, _ = features(perp, days)
if f is None or mkt is None:
    notice("critical", "Missing cached data or IV history. Run scripts/fetch_candles.py and "
                       "scripts/build_iv_history.py, then try again.")
    st.stop()
with st.spinner("Backtesting history..."):
    res = run_option_backtest(strats[name], f, mkt)
obs = observations_from_trades(res.trades, feats.reset_index(drop=True), name)
if not len(obs):
    empty_state("No historical trades for this strategy", "Try another strategy or underlying.")
    st.stop()
current = feats.iloc[-1].to_dict()
an = find_analogues(current, obs, top_k=k)
m = conditional_metrics_from_analogues(an)
kpi_row([{"label": "Analogues", "value": f"{m['sample_size']}"},
         {"label": "Independent days", "value": str(m.get("n_days", "–"))},
         {"label": "Confidence", "value": str(m["confidence_label"])},
         {"label": "Conditional net R", "value": r_mult(m["expected_r"]), "tone": tone(m["expected_r"])},
         {"label": "Win rate", "value": pct(m["win_rate"] * 100, 0) if m["win_rate"] is not None else "–"}])
table(an, height=460)
