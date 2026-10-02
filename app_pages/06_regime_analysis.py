"""Regime Analysis: rule-based regime probabilities and classifier confidence over time."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.regimes.regime_engine import REGIMES, classify_frame
from delta_intelligence.ui import charts
from delta_intelligence.ui.components import empty_state, kpi_row, page_setup, table
from delta_intelligence.ui.research import features

page_setup("Regime Analysis", "Regimes are transparent rules plus a softmax, not machine learning. Trend strength is "
                              "measured in ATR units.")
c = st.columns(2)
perp = c[0].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
days = c[1].slider("Days", 2, 30, 7)
_, feats, _ = features(perp, max(days + 2, 10))
if feats is None:
    empty_state("No data", "Run scripts/fetch_candles.py to fill the candle cache.")
    st.stop()
f = feats.tail(days * 288)
labels, probs = classify_frame(f.iloc[::3])
conf = pd.Series(probs.max(axis=1), index=pd.DatetimeIndex(f["timestamp"].iloc[::3]))
share = pd.Series(labels).value_counts(normalize=True)
kpi_row([{"label": "Current regime", "value": str(labels[-1]) if len(labels) else "–"},
         {"label": "Confidence now", "value": f"{conf.iloc[-1]:.2f}" if len(conf) else "–"},
         {"label": "Mean confidence", "value": f"{conf.mean():.2f}" if len(conf) else "–"},
         {"label": "Most common", "value": f"{share.index[0]} ({share.iloc[0]:.0%})" if len(share) else "–"}])
with st.container(border=True):
    charts.show(charts.regime_probs(f["timestamp"].iloc[::3], probs, REGIMES))
c1, c2 = st.columns([3, 2])
with c1, st.container(border=True):
    charts.show(charts.line({"confidence": conf}, "Confidence (max probability)", refs={"no information 0.10": 0.10}))
with c2:
    table(share.rename("share").rename_axis("regime").reset_index(),
          {"share": st.column_config.ProgressColumn(format="percent", min_value=0.0, max_value=1.0)})
