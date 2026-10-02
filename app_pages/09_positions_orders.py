"""Positions & Orders: open positions with live P&L and manual exit, trade history, orders, fills, analytics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, from_db_time
from delta_intelligence.ui import charts, state
from delta_intelligence.ui.components import (can_control, control_note, empty_state, entity_card, kpi_row, notice,
                                              page_setup, range_bar, table, value_html)
from delta_intelligence.ui.format import money, num, pnl, time_left, tone, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Positions & Orders", "Long options only. Exits: underlying invalidation, premium stop, time stop, and a "
                                 "forced exit before the expiry guard.")
s = get_settings()
rate = s.usdinr_rate
tab_open, tab_hist, tab_orders, tab_fills, tab_an = st.tabs(["Open", "Trade history", "Orders", "Fills", "Analytics"])


@st.fragment(run_every=15)
def open_positions() -> None:
    b = state.broker()
    chain = state.chain()
    items = b.open_positions()
    if not items:
        empty_state("No open positions", "The engine opens positions when an active strategy triggers; you can also "
                                         "buy on Paper Trading.")
        return
    now = now_utc()
    for it in items:
        p, legs = it["position"], it["legs"]
        value, upnl = b.mark(it, chain)
        expiry = from_db_time(p.expiry)
        upct = upnl / p.entry_net_premium * 100 if upnl is not None and p.entry_net_premium else None
        cells = [("Legs", ", ".join(f"{lg.symbol} ×{lg.contracts} @ {lg.entry_price}" for lg in legs)),
                 ("Premium paid", usd(p.entry_net_premium)), ("Value (mid)", usd(value)),
                 ("Expiry (IST)", fmt_ist(expiry, "%d %b %H:%M")), ("Expires in", time_left(expiry, now)),
                 ("Underlying stop", num(p.underlying_stop)), ("Underlying target", num(p.underlying_target)),
                 ("Opened", fmt_ist(from_db_time(p.opened_at), "%d %b %H:%M"))]
        if getattr(p, "time_stop_at", None):
            cells.append(("Time stop in", time_left(from_db_time(p.time_stop_at), now)))
        lo, mid, hi = p.premium_stop_value, p.entry_net_premium, (p.entry_net_premium or 0) * 2

        def body(lo=lo, mid=mid, hi=hi, value=value) -> None:
            if lo and mid and value is not None:
                range_bar(lo, mid, hi, value, "up",
                          (f"premium stop {usd(lo)}", f"entry {usd(mid)}", f"2× entry {usd(hi)}"))

        clicked = entity_card(f"{p.structure} · {p.underlying}", [(p.strategy, "info"), (p.mode, "neutral")],
                              key_value=value_html(upnl, upct), cells=cells, body=body,
                              action={"label": "Exit", "key": f"exit-{p.position_id}", "disabled": not can_control()})
        if clicked:
            r = b.close_position(p.position_id, "MANUAL_EXIT")
            notice("good" if r is not None else "critical",
                   "Closed at the bid." if r is not None else "Could not close: no bid for a leg. Try again shortly.")


with tab_open:
    open_positions()
    control_note()

with db.get_session() as ses:
    closed = ses.query(Position).filter(Position.status != "OPEN").order_by(Position.closed_at).all()
    hist = pd.DataFrame([{"closed (IST)": fmt_ist(from_db_time(p.closed_at), "%d %b %H:%M") if p.closed_at else None,
                          "underlying": p.underlying, "strategy": p.strategy, "structure": p.structure,
                          "premium": p.entry_net_premium, "fees": p.fees, "pnl": p.realized_pnl,
                          "result": pnl(p.realized_pnl),
                          "R": (p.realized_pnl / p.max_loss) if p.max_loss and p.realized_pnl is not None else None,
                          "exit": p.exit_reason, "status": p.status, "mode": p.mode} for p in closed])
    orders = pd.DataFrame([{"time": fmt_ist(from_db_time(o.created_at), "%d %b %H:%M:%S") if o.created_at else None,
                            "symbol": o.symbol, "side": o.side, "size": o.size, "price": o.limit_price,
                            "purpose": o.purpose, "status": o.status, "reason": o.reject_reason,
                            "client_order_id": o.client_order_id}
                           for o in ses.query(Order).order_by(Order.id.desc()).limit(500)])
    fills = pd.DataFrame([{"time": fmt_ist(from_db_time(f.timestamp), "%d %b %H:%M:%S") if f.timestamp else None,
                           "order": f.order_id, "price": f.price, "size": f.size, "fee": f.fee, "gst": f.gst,
                           "mid at fill": f.mid_at_fill} for f in ses.query(Fill).order_by(Fill.id.desc()).limit(500)])

USD = st.column_config.NumberColumn(format="$%.2f")
cfgs = {"trades": {"premium": USD, "fees": USD, "pnl": USD, "R": st.column_config.NumberColumn(format="%+.3f")},
        "orders": {"price": st.column_config.NumberColumn(format="%.2f")},
        "fills": {"price": st.column_config.NumberColumn(format="%.2f"), "fee": st.column_config.NumberColumn(format="$%.4f"),
                  "gst": st.column_config.NumberColumn(format="$%.4f"),
                  "mid at fill": st.column_config.NumberColumn(format="%.2f")}}
for tab, df, name in ((tab_hist, hist, "trades"), (tab_orders, orders, "orders"), (tab_fills, fills, "fills")):
    with tab:
        table(df[::-1] if name == "trades" and len(df) else df, cfgs[name], height=420,
              empty=(f"No {name} yet", "They appear after the first paper or live trade."))
        if len(df):
            st.download_button("Download CSV", df.to_csv(index=False), f"{name}.csv", "text/csv", key=f"dl-{name}",
                               icon=":material/download:")

with tab_an:
    if len(hist) and hist["pnl"].notna().any():
        h = hist.dropna(subset=["pnl"])
        wins, losses = h[h["pnl"] > 0]["pnl"], h[h["pnl"] <= 0]["pnl"]
        net = h["pnl"].sum()
        curve = s.paper_starting_capital_usd + h["pnl"].cumsum()
        curve.index = range(1, len(curve) + 1)
        dd = (curve - np.maximum.accumulate(curve)).min()
        kpi_row([{"label": "Closed trades", "value": f"{len(h)}"},
                 {"label": "Win rate", "value": f"{(h['pnl'] > 0).mean():.0%}"},
                 {"label": "Net P&L", "value": pnl(net), "tone": tone(net), "help": money(net, rate)},
                 {"label": "Profit factor", "value": f"{wins.sum() / -losses.sum():.2f}" if losses.sum() < 0 else "–"},
                 {"label": "Mean R", "value": f"{h['R'].mean():+.3f}" if h["R"].notna().any() else "–"},
                 {"label": "Max drawdown", "value": usd(dd), "help": f"fees paid {usd(h['fees'].sum())}"}])
        c1, c2 = st.columns(2)
        with c1, st.container(border=True):
            charts.show(charts.equity(curve, "Equity after each closed trade (USD)", start=s.paper_starting_capital_usd))
        with c2, st.container(border=True):
            g = h.groupby("strategy")["pnl"].sum()
            charts.show(charts.bars(g.index, g.values, "P&L by strategy (USD)"))
    else:
        empty_state("No closed trades yet", "Analytics appear after the first position closes.")
