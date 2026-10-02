"""Historical Analogues: the past setups most similar to now, for a chosen strategy, with their option outcomes."""
from __future__ import annotations

import streamlit as st

from delta_intelligence.analogues.analogue_engine import conditional_metrics_from_analogues, find_analogues
from delta_intelligence.backtesting.option_backtest import run_option_backtest
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.research.ranking_core import observations_from_trades
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import get_all_strategies
from delta_intelligence.ui.components import page_setup
from delta_intelligence.ui.research import features, market, strategy_frame

page_setup("Historical Analogues")
strats = {s.name: s for s in [v.strategy() for v in ACTIVE_VARIANTS] + get_all_strategies()}
c = st.columns(3)
name = c[0].selectbox("Strategy", list(strats))
perp = c[1].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
k = c[2].slider("Analogues (k)", 10, 60, 30)
if st.button("Find analogues", type="primary"):
    days = 120
    f = strategy_frame(perp, days)
    mkt, _ = market(UNDERLYINGS[perp].asset, perp, days)
    _, feats, _ = features(perp, days)
    if f is None or mkt is None:
        st.error("Missing cached data or IV history.")
        st.stop()
    with st.spinner("Backtesting history..."):
        res = run_option_backtest(strats[name], f, mkt)
    obs = observations_from_trades(res.trades, feats.reset_index(drop=True), name)
    if not len(obs):
        st.info("No historical trades for this strategy.")
        st.stop()
    current = feats.iloc[-1].to_dict()
    an = find_analogues(current, obs, top_k=k)
    m = conditional_metrics_from_analogues(an)
    cc = st.columns(5)
    cc[0].metric("Analogues", m["sample_size"])
    cc[1].metric("Independent days", m.get("n_days", "-"))
    cc[2].metric("Confidence", m["confidence_label"])
    cc[3].metric("Conditional net R", f"{m['expected_r']:+.3f}" if m["expected_r"] is not None else "-")
    cc[4].metric("Win rate", f"{m['win_rate']:.0%}" if m["win_rate"] is not None else "-")
    st.dataframe(an, hide_index=True, width="stretch")
    st.caption("Analogues from the same day are correlated, so confidence counts independent days, not rows.")
