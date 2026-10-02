"""Research Reports: the generated research documents (rendered from docs/research/)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from delta_intelligence.ui.components import empty_state, page_setup

page_setup("Research Reports", "Honest results, including the negative ones. No strategy has a proven edge.")
root = Path(__file__).resolve().parents[1] / "docs" / "research"
files = sorted(root.glob("*.md")) if root.exists() else []
if not files:
    empty_state("No reports yet", "Research scripts write their reports to docs/research/.")
    st.stop()
order = {"STRATEGY_SEARCH.md": 0, "P3V3_REPORT.md": 1, "P3_REPORT.md": 2, "P3A_IV_COVERAGE.md": 3}
files.sort(key=lambda p: order.get(p.name, 9))
choice = st.selectbox("Report", files, format_func=lambda p: p.stem)
with st.container(border=True):
    st.markdown(choice.read_text(encoding="utf-8"))
