"""Positions & Orders: open positions with live P&L and manual exit, trade history, orders, fills, analytics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg, from_db_time
from delta_intelligence.ui import charts, state
from delta_intelligence.ui.components import can_control, control_note, page_setup
from delta_intelligence.ui.format import money, pnl, usd
from delta_intelligence.utils.timeutil import fmt_ist

page_setup("Positions & Orders")
s = get_settings()
rate = s.usdinr_rate
tab_open, tab_hist, tab_orders, tab_fills, tab_an = st.tabs(["Open", "Trade history", "Orders", "Fills", "Analytics"])


@st.fragment(run_every=15)
def open_positions() -> None:
    b = state.broker()
    chain = state.chain()
    items = b.open_positions()
    if not items:
        st.write("No open positions.")
        return
    for it in items:
        p, legs = it["position"], it["legs"]
        value, upnl = b.mark(it, chain)
        with st.container(border=True):
            c = st.columns([3, 2, 2, 2, 1])
            c[0].markdown(f"**{p.structure} {p.underlying}** · {p.strategy}  \n"
                          + ", ".join(f"`{lg.symbol}` ×{lg.contracts} @ {lg.entry_price}" for lg in legs))
            c[1].metric("Value (mid)", usd(value) if value is not None else "-")
            c[2].metric("Unrealised", pnl(upnl) if upnl is not None else "-")
            c[3].markdown(f"Expiry **{fmt_ist(from_db_time(p.expiry), '%d %b %H:%M IST')}**  \n"
                          f"Stop {p.underlying_stop} · Target {p.underlying_target:.2f}" if p.underlying_target else "")
            if c[4].button("Exit", key=f"exit-{p.position_id}", disabled=not can_control()):
                r = b.close_position(p.position_id, "MANUAL_EXIT")
                (st.success if r is not None else st.error)("Closed" if r is not None else "Could not close (no bid)")
            if p.premium_stop_value and value is not None:
                lo, hi = p.premium_stop_value, p.entry_net_premium * 2
                st.progress(min(1.0, max(0.0, (value - lo) / (hi - lo))) if hi > lo else 0.0,
                            text=f"premium stop {usd(lo)} ← now {usd(value)} → 2× entry {usd(hi)}")


with tab_open:
    open_positions()
    control_note()

with db.get_session() as ses:
    closed = ses.query(Position).filter(Position.status != "OPEN").order_by(Position.closed_at).all()
    hist = pd.DataFrame([{"closed (IST)": fmt_ist(from_db_time(p.closed_at), "%d %b %H:%M") if p.closed_at else "",
                          "underlying": p.underlying, "strategy": p.strategy, "structure": p.structure,
                          "premium": p.entry_net_premium, "fees": p.fees, "pnl": p.realized_pnl,
                          "R": (p.realized_pnl / p.max_loss) if p.max_loss else None, "exit": p.exit_reason,
                          "status": p.status, "mode": p.mode} for p in closed])
    orders = pd.DataFrame([{"time": fmt_ist(from_db_time(o.created_at), "%d %b %H:%M:%S") if o.created_at else "",
                            "symbol": o.symbol, "side": o.side, "size": o.size, "price": o.limit_price,
                            "purpose": o.purpose, "status": o.status, "reason": o.reject_reason,
                            "client_order_id": o.client_order_id}
                           for o in ses.query(Order).order_by(Order.id.desc()).limit(500)])
    fills = pd.DataFrame([{"time": fmt_ist(from_db_time(f.timestamp), "%d %b %H:%M:%S") if f.timestamp else "",
                           "order": f.order_id, "price": f.price, "size": f.size, "fee": f.fee, "gst": f.gst,
                           "mid at fill": f.mid_at_fill} for f in ses.query(Fill).order_by(Fill.id.desc()).limit(500)])

for tab, df, name in ((tab_hist, hist, "trades"), (tab_orders, orders, "orders"), (tab_fills, fills, "fills")):
    with tab:
        if len(df):
            st.dataframe(df, hide_index=True, width="stretch")
            st.download_button("Download CSV", df.to_csv(index=False), f"{name}.csv", "text/csv", key=f"dl-{name}")
        else:
            st.write("Nothing yet.")

with tab_an:
    if len(hist) and hist["pnl"].notna().any():
        h = hist.dropna(subset=["pnl"])
        wins, losses = h[h["pnl"] > 0]["pnl"], h[h["pnl"] <= 0]["pnl"]
        c = st.columns(5)
        c[0].metric("Closed trades", len(h))
        c[1].metric("Win rate", f"{(h['pnl'] > 0).mean():.0%}")
        c[2].metric("Net P&L", pnl(h["pnl"].sum(), rate))
        c[3].metric("Profit factor", f"{wins.sum() / -losses.sum():.2f}" if losses.sum() < 0 else "-")
        c[4].metric("Mean R", f"{h['R'].mean():+.3f}")
        curve = s.paper_starting_capital_usd + h["pnl"].cumsum()
        curve.index = range(1, len(curve) + 1)
        st.plotly_chart(charts.equity(curve, "Equity after each closed trade (USD)"), width="stretch")
        dd = (curve - np.maximum.accumulate(curve)).min()
        st.caption(f"Max drawdown {money(dd, rate)} · fees paid {usd(h['fees'].sum())}")
        st.plotly_chart(charts.bars(h.groupby("strategy")["pnl"].sum().index, h.groupby("strategy")["pnl"].sum().values,
                                    "P&L by strategy"), width="stretch")
    else:
        st.write("No closed trades yet.")
