"""Plotly charts on the design tokens. One y-axis per chart, thin lines, series colours in categorical order (identity
only); status colours only where colour means good/bad (up/down candles, gain/loss bars), always with a sign or ▲/▼
label alongside. Reference lines are labelled at the right edge in the line's colour. Render with `show()`."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from delta_intelligence.ui.theme import BORDER, CARD, CRITICAL, FONT, GOOD, MUTED, SERIES, TEXT, TEXT_2, rgba

GRID = "rgba(255,255,255,0.07)"
_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d", "toImage"]}


def _base_layout(fig: go.Figure, height: int = 340, legend: bool | None = None, title: str = "") -> go.Figure:
    n_series = len(fig.data)
    show_legend = legend if legend is not None else n_series >= 2
    top = 8 + (24 if title else 0) + (24 if show_legend else 0)
    fig.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=max(top, 28) if (title or show_legend) else 8, b=8),
        font=dict(family=FONT, size=12, color=TEXT_2), hovermode="x unified",
        hoverlabel=dict(bgcolor=CARD, bordercolor=BORDER, font=dict(family=FONT, color=TEXT)),
        showlegend=show_legend,
        legend=dict(orientation="h", x=0, y=1.0, xanchor="left", yanchor="bottom", bgcolor="rgba(0,0,0,0)"),
        bargap=0.35,
        title=dict(text=title, x=0, xanchor="left", xref="paper", y=1, yref="container", yanchor="top",
                   pad=dict(t=6), font=dict(size=13, color=TEXT)) if title else None,
    )
    axis = dict(gridcolor=GRID, linecolor=BORDER, showline=True, tickfont=dict(color=MUTED), zeroline=False,
                automargin=True)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def hline(fig: go.Figure, y: float, label: str, color: str = MUTED, dash: str = "dot") -> None:
    """Horizontal reference line labelled at the right edge in the line's colour."""
    fig.add_hline(y=y, line=dict(color=color, dash=dash, width=1), annotation_text=label,
                  annotation_position="right", annotation_font_color=color, annotation_xanchor="right")


def vline(fig: go.Figure, x, label: str, color: str = MUTED, dash: str = "dot", position: str = "top") -> None:
    fig.add_vline(x=x, line=dict(color=color, dash=dash, width=1), annotation_text=label,
                  annotation_position=position, annotation_font_color=color)


def show(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", config=_CONFIG, theme=None, key=key)


def _ist(ts) -> pd.Series:
    return pd.to_datetime(pd.Series(ts), utc=True).dt.tz_convert("Asia/Kolkata")


# ---- charts ---------------------------------------------------------------------------------------------------------
def candles(df: pd.DataFrame, overlays: dict[str, pd.Series] | None = None, levels: dict[str, float] | None = None,
            title: str = "", height: int = 400) -> go.Figure:
    x = _ist(df["timestamp"])
    fig = go.Figure(go.Candlestick(x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="price",
                                   increasing=dict(line=dict(color=GOOD, width=1), fillcolor=GOOD),
                                   decreasing=dict(line=dict(color=CRITICAL, width=1), fillcolor=CRITICAL)))
    for (name, series), color in zip((overlays or {}).items(), SERIES):
        fig.add_trace(go.Scatter(x=x, y=series.to_numpy(), name=name, line=dict(color=color, width=1.4)))
    for name, lvl in (levels or {}).items():
        if lvl is not None and np.isfinite(lvl):
            hline(fig, lvl, name)
    fig.update_layout(xaxis_rangeslider_visible=False)
    return _base_layout(fig, height, title=title)


def payoff(spots: np.ndarray, pnl_expiry: np.ndarray, pnl_now: np.ndarray | None, spot: float,
           breakevens: list[float], title: str = "") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=spots, y=pnl_expiry, name="P&L at expiry", line=dict(color=SERIES[0], width=1.6)))
    if pnl_now is not None:
        fig.add_trace(go.Scatter(x=spots, y=pnl_now, name="P&L now (model)",
                                 line=dict(color=SERIES[1], width=1.4, dash="dash")))
    hline(fig, 0, "0", MUTED, "solid")
    vline(fig, spot, f"spot {spot:,.0f}", SERIES[2], "solid", "top left")
    for b in breakevens:
        vline(fig, b, f"BE {b:,.0f}", position="bottom right")
    fig.update_yaxes(title_text="USD")
    return _base_layout(fig, 320, title=title)


def equity(curve: pd.Series, title: str = "", start: float | None = None, height: int = 300) -> go.Figure:
    fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, name="equity", fill="tozeroy" if start is None else None,
                               line=dict(color=SERIES[0], width=1.6), fillcolor=rgba(SERIES[0], .10)))
    if start is not None:
        hline(fig, start, f"start {start:,.0f}")
    fig.update_yaxes(rangemode="normal")
    return _base_layout(fig, height, title=title)


def cumulative_r(curve: pd.Series, title: str = "") -> go.Figure:
    fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, name="cumulative net R",
                               line=dict(color=SERIES[0], width=1.6)))
    hline(fig, 0, "0 R", MUTED, "solid")
    return _base_layout(fig, 300, title=title)


def regime_probs(ts: pd.Series, probs: np.ndarray, names: list[str], top: int = 4) -> go.Figure:
    """Stacked probabilities: the `top` regimes by mean probability in the categorical colours, the rest summed as
    'other' (muted), so no two series share a colour."""
    fig = go.Figure()
    x = _ist(ts)
    order = list(np.argsort(-probs.mean(axis=0))[:top])
    for i, c in zip(order, SERIES):
        fig.add_trace(go.Scatter(x=x, y=probs[:, i], name=names[i], stackgroup="one", line=dict(width=0.6, color=c),
                                 fillcolor=rgba(c, .6)))
    rest = [i for i in range(len(names)) if i not in order]
    if rest:
        fig.add_trace(go.Scatter(x=x, y=probs[:, rest].sum(axis=1), name=f"other ({len(rest)})", stackgroup="one",
                                 line=dict(width=0.6, color=MUTED), fillcolor=rgba(MUTED, .35)))
    fig.update_yaxes(range=[0, 1], title_text="probability")
    return _base_layout(fig, 340)


def line(series: dict[str, pd.Series], title: str = "", height: int = 220, ist: bool = True,
         refs: dict[str, float] | None = None) -> go.Figure:
    """One or more lines on ONE y-axis (don't mix units)."""
    fig = go.Figure()
    for (name, s), c in zip(series.items(), SERIES):
        x = _ist(s.index) if ist else s.index
        fig.add_trace(go.Scatter(x=x, y=s.to_numpy(), name=name, line=dict(color=c, width=1.5)))
    for name, y in (refs or {}).items():
        hline(fig, y, name)
    return _base_layout(fig, height, title=title)


def hbar(values: pd.Series, title: str = "", height: int | None = None, fmt: str = ".2f") -> go.Figure:
    """Horizontal bars for identity data (e.g. regime probabilities): one series colour, value labels."""
    v = values.sort_values()
    fig = go.Figure(go.Bar(x=v.to_numpy(), y=[str(i) for i in v.index], orientation="h", marker_color=SERIES[0],
                           text=[format(x, fmt) for x in v.to_numpy()], textposition="outside", cliponaxis=False))
    return _base_layout(fig, height or 40 + 26 * len(v), legend=False, title=title)


def bars(labels, values, title: str = "", good_bad: bool = True) -> go.Figure:
    """Vertical bars. With good_bad, gains are green and losses red AND labelled '▲ +x' / '▼ -x'."""
    vals = list(values)
    if good_bad:
        colors = [GOOD if v >= 0 else CRITICAL for v in vals]
        text = [f"{'▲' if v > 0 else '▼' if v < 0 else '–'} {v:+,.2f}" for v in vals]
    else:
        colors, text = SERIES[0], [f"{v:,.2f}" for v in vals]
    fig = go.Figure(go.Bar(x=[str(x) for x in labels], y=vals, marker_color=colors, text=text, textposition="outside",
                           cliponaxis=False))
    hline(fig, 0, "", MUTED, "solid")
    return _base_layout(fig, 300, legend=False, title=title)
