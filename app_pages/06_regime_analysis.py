"""Regime Analysis: rule-based regime probabilities and classifier confidence over time."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.regimes.regime_engine import REGIMES, classify_frame
from delta_intelligence.ui import charts
from delta_intelligence.ui.components import page_setup
from delta_intelligence.ui.research import features

page_setup("Regime Analysis")
c = st.columns(2)
perp = c[0].selectbox("Underlying", list(get_watchlist()), format_func=lambda x: UNDERLYINGS[x].asset)
days = c[1].slider("Days", 2, 30, 7)
_, feats, _ = features(perp, max(days + 2, 10))
if feats is None:
    st.info("No data.")
    st.stop()
f = feats.tail(days * 288)
labels, probs = classify_frame(f.iloc[::3])
st.plotly_chart(charts.regime_probs(f["timestamp"].iloc[::3], probs, REGIMES), width="stretch")
conf = pd.Series(probs.max(axis=1), index=f["timestamp"].iloc[::3])
st.line_chart(conf.rename("confidence (max probability; 0.10 = no information)"), height=180)
st.dataframe(pd.Series(labels).value_counts(normalize=True).rename("share").to_frame(), width="stretch")
st.caption("Regimes are transparent rules plus a softmax, not machine learning. Trend strength is measured in ATR units.")
