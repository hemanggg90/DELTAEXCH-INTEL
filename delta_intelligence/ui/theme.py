"""Design tokens: the single source of truth for every colour in the dashboard, mirrored as CSS custom properties
(--app-*). Pages never use colours or CSS directly; they call ui.components / ui.charts, which read these tokens.

Categorical series colours mean identity only (series 1, 2, 3...). Status colours are reserved for good/bad and always
come with an icon, sign or label: colour is never the only signal."""
from __future__ import annotations

import streamlit as st

# surfaces
SURFACE, CARD, CARD_2, BORDER = "#0e1117", "#151a23", "#1b2130", "#262d3b"
# text
TEXT, TEXT_2, MUTED = "#e8eaed", "#aab0bc", "#7d8594"
# categorical series (identity only, in this order)
BLUE, ORANGE, AQUA, VIOLET = "#3987e5", "#d95926", "#199e70", "#9085e9"
SERIES = (BLUE, ORANGE, AQUA, VIOLET)
# status (reserved: only where colour means good/bad)
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"

TONE_COLOR = {"good": GOOD, "warning": WARNING, "serious": SERIOUS, "critical": CRITICAL, "info": BLUE,
              "neutral": MUTED, "muted": MUTED}
TONE_ICON = {"good": "●", "warning": "▲", "serious": "▲", "critical": "✖", "info": "●", "neutral": "○", "muted": "○"}
FONT = "Inter, Segoe UI, system-ui, sans-serif"


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def tone_color(tone: str) -> str:
    return TONE_COLOR.get(tone, MUTED)


def tone_icon(tone: str) -> str:
    return TONE_ICON.get(tone, "○")


def _vars() -> str:
    tokens = {"surface": SURFACE, "card": CARD, "card-2": CARD_2, "border": BORDER, "text": TEXT, "text-2": TEXT_2,
              "muted": MUTED, "blue": BLUE, "orange": ORANGE, "aqua": AQUA, "violet": VIOLET, "good": GOOD,
              "warning": WARNING, "serious": SERIOUS, "critical": CRITICAL}
    return ";".join(f"--app-{k}:{v}" for k, v in tokens.items())


def _tone_classes() -> str:
    out = []
    for tone, c in TONE_COLOR.items():
        out.append(f".app-chip.t-{tone}{{color:{c};background:{rgba(c, .12)};border-color:{rgba(c, .45)}}}"
                   f".app-t-{tone}{{color:{c}}}.app-meter .t-{tone}{{background:{c}}}")
    return "".join(out)


CSS = f"""
<style>
:root{{{_vars()}}}
.block-container{{padding-top:2.2rem;padding-bottom:3rem;max-width:1500px}}
h1{{font-size:1.7rem!important;font-weight:650!important;letter-spacing:-0.01em}}
h2{{font-size:1.2rem!important;font-weight:620!important}}
h3{{font-size:1.02rem!important;font-weight:620!important;color:var(--app-text)}}
[data-testid="stMetric"]{{background:var(--app-card);border-radius:10px}}
[data-testid="stMetricValue"]{{font-size:1.45rem!important;font-weight:600;font-variant-numeric:tabular-nums}}
[data-testid="stMetricLabel"] p{{font-size:.78rem!important;text-transform:uppercase;letter-spacing:.04em;
  color:var(--app-text-2)}}
[data-testid="stMetricDelta"]{{font-variant-numeric:tabular-nums}}
[data-testid="stDataFrame"],[data-testid="stTable"],.app-num{{font-variant-numeric:tabular-nums}}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]){{border-radius:10px}}
div[data-testid="stVerticalBlock"][class*="border"],[data-testid="stExpander"] details{{background:var(--app-card);
  border-color:var(--app-border)!important;border-radius:10px}}
.app-bar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:7px 12px;margin:2px 0 14px;
  background:var(--app-card);border:1px solid var(--app-border);border-radius:10px;font-size:.82rem;
  color:var(--app-text-2)}}
.app-clock{{font-weight:700;color:var(--app-text);font-variant-numeric:tabular-nums}}
.app-div{{width:1px;height:18px;background:var(--app-border)}}
.app-chip{{display:inline-flex;align-items:center;gap:4px;font-size:.74rem;font-weight:600;border-radius:999px;
  padding:2px 9px;border:1px solid;white-space:nowrap;font-variant-numeric:tabular-nums}}
.app-card-head{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;justify-content:space-between}}
.app-card-title{{font-weight:650;font-size:1rem;color:var(--app-text);display:flex;flex-wrap:wrap;gap:6px;
  align-items:center}}
.app-card-key{{font-weight:650;font-size:1.05rem;font-variant-numeric:tabular-nums;margin-left:auto}}
.app-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(105px,1fr));gap:6px 14px;margin-top:8px}}
.app-cell-l{{font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;color:var(--app-muted)}}
.app-cell-v{{font-size:.95rem;font-weight:600;color:var(--app-text);font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}}
.app-sub{{font-size:.8rem;color:var(--app-text-2);margin-top:4px}}
.app-empty{{text-align:center;padding:28px;border:1px dashed var(--app-border);border-radius:10px;
  color:var(--app-text-2);margin:6px 0}}
.app-empty b{{color:var(--app-text);display:block;margin-bottom:4px}}
.app-meter{{height:6px;border-radius:99px;background:var(--app-card-2);overflow:hidden;margin:6px 0 2px}}
.app-meter div{{height:100%;border-radius:99px}}
.app-kv{{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:.88rem}}
.app-kv .k{{color:var(--app-text-2)}} .app-kv .v{{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
.app-range{{position:relative;height:16px;margin:8px 0 2px}}
.app-range .track{{position:absolute;top:6px;left:0;right:0;height:4px;border-radius:99px;
  background:var(--app-card-2)}}
.app-range .fill{{position:absolute;top:6px;height:4px}}
.app-range .tick{{position:absolute;top:2px;width:2px;height:12px;background:var(--app-text-2)}}
.app-range .dot{{position:absolute;top:2px;width:12px;height:12px;border-radius:50%;margin-left:-6px;
  border:2px solid var(--app-surface)}}
.app-range-l{{display:flex;justify-content:space-between;font-size:.72rem;color:var(--app-muted);
  font-variant-numeric:tabular-nums}}
.app-quote-name{{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:var(--app-text-2)}}
.app-quote-px{{font-size:2rem;font-weight:700;color:var(--app-text);font-variant-numeric:tabular-nums;
  line-height:1.15}}
.app-quote-ch{{font-size:1rem;font-weight:600;margin-left:10px;font-variant-numeric:tabular-nums}}
.app-val{{font-weight:600;font-variant-numeric:tabular-nums}} .app-val small{{opacity:.8;margin-left:4px}}
{_tone_classes()}
@media (max-width: 640px){{
  .block-container{{padding-left:.8rem;padding-right:.8rem;padding-top:1.4rem}}
  .app-bar{{font-size:.75rem}} .app-div{{display:none}} .app-quote-px{{font-size:1.6rem}}
  [data-testid="stMetricValue"]{{font-size:1.2rem!important}}
}}
</style>
"""


def apply_theme(page_title: str = "delta-intelligence", page_icon: str = "📈") -> None:
    """Call first on every page: wide layout, title, and the one global <style> block."""
    st.set_page_config(page_title=f"{page_title} · delta-intelligence" if page_title != "delta-intelligence"
                       else page_title, page_icon=page_icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
