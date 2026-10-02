"""Backtest Lab: run any strategy as bought options on cached data, with editable parameters, IS/OOS and folds."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.backtesting.option_backtest import (OptionBacktestConfig, run_option_backtest, split_in_out,
                                                            summarize_trades, time_folds, validation_stats)
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.options.selector import SelectorConfig
from delta_intelligence.strategies import legacy_v2_library
from delta_intelligence.strategies.registry import STRATEGY_CLASSES
from delta_intelligence.ui import charts
from delta_intelligence.ui.components import page_setup
from delta_intelligence.ui.research import market, strategy_frame

page_setup("Backtest Lab")
st.caption("Option premiums are MODELLED (Black-Scholes at as-of IV inferred from real Delta trades, plus the fitted "
           "smile and 75th-percentile spreads), not recorded quotes. Changing parameters until something looks good is "
           "overfitting: judge only on data you didn't tune on.")
classes = {**STRATEGY_CLASSES, **{c.name + " (v2)": c for c in legacy_v2_library.ALL_STRATEGIES}}
c = st.columns(4)
name = c[0].selectbox("Strategy", list(classes))
perp = c[1].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
days = c[2].slider("Days of history", 30, 300, 120, step=10)
money_ = c[3].radio("Strike", ["ATM", "ITM1"], horizontal=True)
cls = classes[name]
params = {}
with st.expander("Parameters"):
    for k, v in cls.default_parameters.items():
        params[k] = st.number_input(k, value=float(v), key=f"p-{k}")
if st.button("Run backtest", type="primary"):
    f = strategy_frame(perp, days)
    mkt, real = market(UNDERLYINGS[perp].asset, perp, days)
    if f is None or mkt is None:
        st.error("Missing cached data or IV history (run scripts/fetch_candles.py and scripts/build_iv_history.py).")
        st.stop()
    strat = cls({k: (int(v) if float(v).is_integer() and isinstance(cls.default_parameters[k], int) else v)
                 for k, v in params.items()})
    with st.spinner("Backtesting..."):
        res = run_option_backtest(strat, f, mkt, OptionBacktestConfig(selector=SelectorConfig(moneyness=money_)), real=real)
    sm = summarize_trades(res.trades)
    if not sm.get("n_trades"):
        st.warning(f"No trades. Setups {res.n_setups}; skipped {dict(res.skipped)}")
        st.stop()
    ins, oos = split_in_out(res.trades)
    k = st.columns(6)
    k[0].metric("Trades", sm["n_trades"])
    k[1].metric("Win rate", f"{sm['win_rate']:.0%}")
    k[2].metric("Net R / trade", f"{sm['net_expected_r']:+.3f}")
    k[3].metric("In-sample R", f"{summarize_trades(ins).get('net_expected_r', 0):+.3f}")
    k[4].metric("Out-of-sample R", f"{summarize_trades(oos).get('net_expected_r', 0):+.3f}")
    k[5].metric("Max DD (R)", f"{sm['max_drawdown_r']:.1f}")
    curve = pd.Series([t.net_r for t in res.trades]).cumsum()
    curve.index = [t.entry_time for t in res.trades]
    st.plotly_chart(charts.equity(curve, "Cumulative net R"), width="stretch")
    folds = [summarize_trades(x).get("net_expected_r") for x in time_folds(res.trades, 4)]
    st.write("Time folds (net R):", ", ".join("-" if x is None else f"{x:+.3f}" for x in folds))
    st.write("Exit reasons:", sm["exit_reasons"], " · premium sources:", sm["premium_sources"],
             " · skipped setups:", dict(res.skipped), " · model vs real:", validation_stats(res.trades))
    st.dataframe(res.frame(), hide_index=True, width="stretch")
