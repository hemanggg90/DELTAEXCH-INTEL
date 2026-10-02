"""Strategy Library: every strategy's description, family, parameters, expiry policy and status."""
from __future__ import annotations

import streamlit as st

from delta_intelligence.strategies import legacy_v2_library
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import get_all_strategies
from delta_intelligence.ui.components import page_setup

page_setup("Strategy Library")
st.markdown("All strategies are executed by **buying options** (calls for LONG, puts for SHORT, straddles for the "
            "event strategy). The expiry is chosen so that time to expiry is ≥ 2.5 × the expected hold.")

st.subheader("Active (paper) variants")
for v in ACTIVE_VARIANTS:
    s = v.strategy()
    with st.expander(f"🟢 {v.key}"):
        st.write(s.description)
        st.json({"class": v.cls.__name__, "params": s.parameters, "moneyness": v.moneyness,
                 "expected_hold_h": s.expected_hold_bars * 5 / 60, "time_stop_h": s.max_hold_bars * 5 / 60})

st.subheader("v3 library (research)")
for s in get_all_strategies():
    with st.expander(f"{s.name} · {s.family} · {s.policy}"):
        st.write(s.description)
        st.json({"params": s.parameters, "key_parameters": list(getattr(s, "key_parameters", ())),
                 "expected_hold_h": s.expected_hold_bars * 5 / 60, "time_stop_h": s.max_hold_bars * 5 / 60,
                 "event_strategy": s.is_event})

st.subheader("v2 library (archived: no edge found)")
for cls in legacy_v2_library.ALL_STRATEGIES:
    s = cls()
    with st.expander(f"{s.name} · {s.family}"):
        st.write(s.description)
        st.json({"params": s.parameters})
