"""Plotly charts. Palette: blue/orange/aqua for series; green/red reserved for good/bad. One axis per chart, hairline
grids."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

BLUE, ORANGE, AQUA, GREEN, RED, GREY = "#2563eb", "#f97316", "#06b6d4", "#16a34a", "#dc2626", "#9ca3af"


def _layout(fig: go.Figure, title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(title=title, height=height, margin=dict(l=10, r=10, t=40 if title else 10, b=10),
                      legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
    fig.update_xaxes(showgrid=True, gridwidth=0.5, gridcolor="rgba(128,128,128,.15)")
    fig.update_yaxes(showgrid=True, gridwidth=0.5, gridcolor="rgba(128,128,128,.15)")
    return fig


def candles(df: pd.DataFrame, overlays: dict[str, pd.Series] | None = None, levels: dict[str, float] | None = None,
            title: str = "") -> go.Figure:
    x = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    fig = go.Figure(go.Candlestick(x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                   increasing_line_color=GREEN, decreasing_line_color=RED, name="price"))
    for (name, series), color in zip((overlays or {}).items(), (BLUE, ORANGE, AQUA)):
        fig.add_trace(go.Scatter(x=x, y=series, name=name, line=dict(color=color, width=1.2)))
    for name, lvl in (levels or {}).items():
        if lvl is not None and np.isfinite(lvl):
            fig.add_hline(y=lvl, line=dict(color=GREY, dash="dot", width=1), annotation_text=name)
    fig.update_layout(xaxis_rangeslider_visible=False)
    return _layout(fig, title)


def payoff(spots: np.ndarray, pnl_expiry: np.ndarray, pnl_now: np.ndarray | None, spot: float,
           breakevens: list[float], title: str = "Payoff") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=spots, y=pnl_expiry, name="at expiry", line=dict(color=BLUE, width=2)))
    if pnl_now is not None:
        fig.add_trace(go.Scatter(x=spots, y=pnl_now, name="now (model)", line=dict(color=ORANGE, width=1.5, dash="dash")))
    fig.add_hline(y=0, line=dict(color=GREY, width=1))
    fig.add_vline(x=spot, line=dict(color=AQUA, width=1), annotation_text="spot")
    for b in breakevens:
        fig.add_vline(x=b, line=dict(color=GREY, dash="dot", width=1), annotation_text=f"BE {b:,.0f}")
    fig.update_yaxes(title="P&L (USD)")
    return _layout(fig, title, 340)


def equity(curve: pd.Series, title: str = "Equity (USD)") -> go.Figure:
    fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, line=dict(color=BLUE, width=2), name="equity"))
    return _layout(fig, title, 300)


def regime_probs(ts: pd.Series, probs: np.ndarray, names: list[str]) -> go.Figure:
    fig = go.Figure()
    x = pd.to_datetime(ts, utc=True).dt.tz_convert("Asia/Kolkata")
    for i, n in enumerate(names):
        fig.add_trace(go.Scatter(x=x, y=probs[:, i], name=n, stackgroup="one", line=dict(width=0.5)))
    fig.update_yaxes(range=[0, 1], title="probability")
    return _layout(fig, "Regime probabilities", 360)


def bars(labels, values, title: str = "", good_bad: bool = True) -> go.Figure:
    colors = [GREEN if v >= 0 else RED for v in values] if good_bad else BLUE
    fig = go.Figure(go.Bar(x=list(labels), y=list(values), marker_color=colors))
    return _layout(fig, title, 300)
