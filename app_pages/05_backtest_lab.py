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
from delta_intelligence.ui.components import empty_state, kpi_row, kv_grid, notice, page_setup, table
from delta_intelligence.ui.format import r_mult, tone
from delta_intelligence.ui.research import market, strategy_frame

page_setup("Backtest Lab", "Premiums are MODELLED (Black-Scholes at as-of IV inferred from real Delta trades, plus the "
                           "fitted smile and 75th-percentile spreads), not recorded quotes. Tuning until something looks "
                           "good is overfitting: judge only on data you didn't tune on.")
classes = {**STRATEGY_CLASSES, **{c.name + " (v2)": c for c in legacy_v2_library.ALL_STRATEGIES}}
with st.container(border=True):
    c = st.columns(4)
    name = c[0].selectbox("Strategy", list(classes))
    perp = c[1].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
    days = c[2].slider("Days of history", 30, 300, 120, step=10)
    money_ = c[3].segmented_control("Strike", ["ATM", "ITM1"], default="ATM") or "ATM"
    cls = classes[name]
    params = {}
    with st.expander("Parameters"):
        pc = st.columns(4)
        for i, (k, v) in enumerate(cls.default_parameters.items()):
            params[k] = pc[i % 4].number_input(k, value=float(v), key=f"p-{k}")
    run = st.button("Run backtest", type="primary", icon=":material/play_arrow:")

if not run:
    empty_state("No backtest run yet", "Pick a strategy, underlying and window, then press Run backtest.")
    st.stop()
f = strategy_frame(perp, days)
mkt, real = market(UNDERLYINGS[perp].asset, perp, days)
if f is None or mkt is None:
    notice("critical", "Missing cached data or IV history. Run scripts/fetch_candles.py and "
                       "scripts/build_iv_history.py, then try again.")
    st.stop()
strat = cls({k: (int(v) if float(v).is_integer() and isinstance(cls.default_parameters[k], int) else v)
             for k, v in params.items()})
with st.spinner("Backtesting..."):
    res = run_option_backtest(strat, f, mkt, OptionBacktestConfig(selector=SelectorConfig(moneyness=money_)), real=real)
sm = summarize_trades(res.trades)
if not sm.get("n_trades"):
    empty_state("No trades", f"Setups {res.n_setups}; skipped {dict(res.skipped)}")
    st.stop()
ins, oos = split_in_out(res.trades)
r_is, r_oos = summarize_trades(ins).get("net_expected_r", 0), summarize_trades(oos).get("net_expected_r", 0)
kpi_row([
    {"label": "Trades", "value": f"{sm['n_trades']}"},
    {"label": "Win rate", "value": f"{sm['win_rate']:.0%}"},
    {"label": "Net R / trade", "value": r_mult(sm["net_expected_r"]), "tone": tone(sm["net_expected_r"])},
    {"label": "In-sample R", "value": r_mult(r_is), "tone": tone(r_is)},
    {"label": "Out-of-sample R", "value": r_mult(r_oos), "tone": tone(r_oos)},
    {"label": "Max DD (R)", "value": f"{sm['max_drawdown_r']:.1f}"},
])
curve = pd.Series([t.net_r for t in res.trades]).cumsum()
curve.index = [t.entry_time for t in res.trades]
c1, c2 = st.columns([3, 2])
with c1, st.container(border=True):
    charts.show(charts.cumulative_r(curve, "Cumulative net R"))
with c2, st.container(border=True):
    folds = [summarize_trades(x).get("net_expected_r") for x in time_folds(res.trades, 4)]
    kv_grid([(f"Fold {i + 1} net R", r_mult(x)) for i, x in enumerate(folds)]
            + [("Exit reasons", ", ".join(f"{k} {v}" for k, v in sm["exit_reasons"].items())),
               ("Premium sources", ", ".join(f"{k} {v}" for k, v in sm["premium_sources"].items())),
               ("Skipped setups", ", ".join(f"{k} {v}" for k, v in dict(res.skipped).items()) or "–"),
               ("Model vs real", str(validation_stats(res.trades)))])
table(res.frame(), height=420)
