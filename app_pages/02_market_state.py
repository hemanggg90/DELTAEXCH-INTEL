"""Market State: the latest feature vector (each documented), regime probabilities and data quality."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.features.feature_engine import FEATURE_DEFINITIONS
from delta_intelligence.market_state.market_state_engine import build_current_state
from delta_intelligence.ui import charts
from delta_intelligence.ui.components import empty_state, kpi_row, page_setup, table
from delta_intelligence.ui.format import num, text
from delta_intelligence.ui.ranker_view import iv_panel
from delta_intelligence.ui.research import features
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Market State", "Causal features on closed 5m bars (no look-ahead) and transparent rule-based regimes.")
perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda p: UNDERLYINGS[p].asset)
ohlcv, feats, quality = features(perp, 30)
if feats is None:
    empty_state("No data available", "Run scripts/fetch_candles.py to fill the candle cache.")
    st.stop()
state_ = build_current_state(perp, feats, quality)
if state_ is None:
    empty_state("Not enough history for a state yet", "Features need a warm-up window of closed bars.")
    st.stop()


def _val(v) -> str:
    return num(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else text(v)


ok = str(quality).upper() in ("OK", "PASS")
kpi_row([
    {"label": "Latest closed bar", "value": fmt_ist(state_.timestamp, "%d %b %H:%M IST")},
    {"label": "Data quality", "value": str(quality), "delta": "● ok" if ok else "▲ check", "tone": "good" if ok else "neutral"},
    {"label": "Regime", "value": state_.regime},
    {"label": "Regime confidence", "value": f"{state_.regime_confidence:.2f}",
     "help": "Max softmax probability; 0.10 = no information."},
])
st.subheader("Implied volatility")
iv_panel(UNDERLYINGS[perp].asset)
c1, c2 = st.columns([2, 3])
with c1, st.container(border=True):
    charts.show(charts.hbar(pd.Series(state_.regime_probabilities), "Regime probabilities"))
with c2:
    rows = [{"feature": k, "value": _val(state_.features.get(k)), "definition": FEATURE_DEFINITIONS.get(k, "")}
            for k in FEATURE_DEFINITIONS]
    table(pd.DataFrame(rows), height=520)
