"""Market State: the latest feature vector (each documented), regime probabilities and data quality."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.features.feature_engine import FEATURE_DEFINITIONS
from delta_intelligence.market_state.market_state_engine import build_current_state
from delta_intelligence.ui.components import page_setup
from delta_intelligence.ui.research import features
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Market State")
perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda p: UNDERLYINGS[p].asset)
ohlcv, feats, quality = features(perp, 30)
if feats is None:
    st.info("No data available.")
    st.stop()
state_ = build_current_state(perp, feats, quality)
if state_ is None:
    st.info("Not enough history for a state yet.")
    st.stop()
c = st.columns(4)
c[0].metric("Latest closed bar", fmt_ist(state_.timestamp, "%d %b %H:%M IST"))
c[1].metric("Data quality", quality)
c[2].metric("Regime", state_.regime)
c[3].metric("Regime confidence", f"{state_.regime_confidence:.2f}")
st.bar_chart(pd.Series(state_.regime_probabilities).sort_values(ascending=False), height=220)
rows = [{"feature": k, "value": state_.features.get(k), "definition": FEATURE_DEFINITIONS.get(k, "")}
        for k in FEATURE_DEFINITIONS]
st.dataframe(pd.DataFrame(rows).astype({"value": str}), hide_index=True, width="stretch", height=600)
