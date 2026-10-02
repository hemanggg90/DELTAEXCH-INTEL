"""Strategy Library: every strategy's description, family, parameters, expiry policy and status."""
from __future__ import annotations

import streamlit as st

from delta_intelligence.strategies import legacy_v2_library
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import get_all_strategies
from delta_intelligence.ui.components import entity_card, page_setup
from delta_intelligence.ui.format import num

page_setup("Strategy Library", "Every strategy is executed by BUYING options: calls for LONG, puts for SHORT, straddles "
                               "for the event strategy. Time to expiry ≥ 2.5 × the expected hold.")


def _params(p: dict) -> list[tuple[str, str]]:
    return [(k.replace("_", " "), num(v, 2) if isinstance(v, float) else str(v)) for k, v in p.items()]


view = st.segmented_control("Library", ["Active (paper)", "v3 library (research)", "v2 library (archived)"],
                            default="Active (paper)", label_visibility="collapsed") or "Active (paper)"
cols = st.columns(2)
if view.startswith("Active"):
    for i, v in enumerate(ACTIVE_VARIANTS):
        s = v.strategy()
        with cols[i % 2]:
            entity_card(v.key, [("active · paper", "good"), (v.moneyness, "neutral")],
                        key_value=f"hold {s.expected_hold_bars * 5 / 60:.1f}h", sub=s.description,
                        cells=[("class", v.cls.__name__), ("time stop", f"{s.max_hold_bars * 5 / 60:.1f}h")]
                        + _params(s.parameters))
elif view.startswith("v3"):
    for i, s in enumerate(get_all_strategies()):
        with cols[i % 2]:
            entity_card(s.name, [(s.family, "neutral"), (s.policy, "neutral")]
                        + ([("event", "info")] if s.is_event else []),
                        key_value=f"hold {s.expected_hold_bars * 5 / 60:.1f}h", sub=s.description,
                        cells=[("time stop", f"{s.max_hold_bars * 5 / 60:.1f}h"),
                               ("key params", ", ".join(getattr(s, "key_parameters", ())) or "–")]
                        + _params(s.parameters))
else:
    for i, cls in enumerate(legacy_v2_library.ALL_STRATEGIES):
        s = cls()
        with cols[i % 2]:
            entity_card(s.name, [(s.family, "neutral"), ("archived · no edge", "muted")], sub=s.description,
                        cells=_params(s.parameters))
