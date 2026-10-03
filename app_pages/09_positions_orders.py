"""Positions & Orders: open positions with live P&L and manual exit, trade history, orders, fills, analytics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, from_db_time
from delta_intelligence.ui import charts, state
from delta_intelligence.ui.amounts import order_amount, position_amount_used, used_today
from delta_intelligence.ui.components import (can_control, control_note, empty_state, entity_card, kpi_row, notice,
                                              page_setup, range_bar, table, value_html)
from delta_intelligence.ui.format import MISSING, inr, money, num, pnl, pnl_inr, time_left, tone, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Positions & Orders", "Long options only. Exits: underlying invalidation, premium stop, time stop, and a "
                                 "forced exit before the expiry guard.")
s = get_settings()
rate = s.usdinr_rate


@st.fragment(run_every=15)
def summary() -> None:
    """Amount used and P&L, USD and INR (estimate). 'Today' = risk day since 00:00 IST."""
    b = state.broker()
    chain = state.chain()
    c = s.clocks
    today = used_today(now_utc(), getattr(b, "mode", "PAPER"), c.risk_day_tz, c.risk_day_start)
    open_used, open_upnl = 0.0, None
    for it in b.open_positions():
        open_used += position_amount_used(it["position"]) or 0.0
        _, upnl = b.mark(it, chain)
        if upnl is not None:
            open_upnl = (open_upnl or 0.0) + upnl
    kpi_row([
        {"label": "Amount used today", "value": usd(today["used"]), "delta": f"≈ {inr(today['used'], rate)}",
         "help": f"Premium + entry fees of the {today['trades']} position(s) opened since 00:00 IST"},
        {"label": "Realised P&L today", "value": pnl(today["realized"]), "tone": tone(today["realized"]),
         "delta": pnl_inr(today["realized"], rate) if today["realized"] is not None else None,
         "help": "Closed positions among those opened since 00:00 IST"},
        {"label": "Open amount used", "value": usd(open_used), "delta": f"≈ {inr(open_used, rate)}",
         "help": "Premium + entry fees of the positions still open"},
        {"label": "Unrealised P&L", "value": pnl(open_upnl), "tone": tone(open_upnl),
         "delta": pnl_inr(open_upnl, rate) if open_upnl is not None else None, "help": "Open positions at mid"},
    ])
    st.caption(f"₹ figures are estimates at ₹{rate:,.2f}/$ (USDINR_RATE). The book settles in USD.")


summary()
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
                 ("Amount used", money(position_amount_used(p), rate)),
                 ("Premium paid", usd(p.entry_net_premium)), ("Value (mid)", money(value, rate)),
                 ("P&L (₹ est.)", pnl_inr(upnl, rate)),
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
                          "amount used": position_amount_used(p),
                          "amount used (₹ est.)": inr(position_amount_used(p), rate),
                          "premium": p.entry_net_premium, "fees": p.fees, "pnl": p.realized_pnl,
                          "result": pnl(p.realized_pnl), "pnl (₹ est.)": pnl_inr(p.realized_pnl, rate),
                          "R": (p.realized_pnl / p.max_loss) if p.max_loss and p.realized_pnl is not None else None,
                          "exit": p.exit_reason, "status": p.status, "mode": p.mode} for p in closed])
    cv = dict(ses.query(Position.position_id, Position.contract_value).all())  # contract value per position
    order_rows = ses.query(Order).order_by(Order.id.desc()).limit(500).all()
    fill_rows = ses.query(Fill).order_by(Fill.id.desc()).limit(500).all()
    order_pos = dict(ses.query(Order.order_id, Order.position_id)
                     .filter(Order.order_id.in_({f.order_id for f in fill_rows})).all()) if fill_rows else {}

    def _order_row(o: Order) -> dict:
        amt = order_amount(o.limit_price, o.size, cv.get(o.position_id))
        return {"time": fmt_ist(from_db_time(o.created_at), "%d %b %H:%M:%S") if o.created_at else None,
                "symbol": o.symbol, "side": o.side, "size": o.size, "price": o.limit_price,
                "amount": amt, "amount (₹ est.)": inr(amt, rate) if amt is not None else MISSING,
                "purpose": o.purpose, "status": o.status, "reason": o.reject_reason,
                "client_order_id": o.client_order_id}

    def _fill_row(f: Fill) -> dict:
        gross = order_amount(f.price, f.size, cv.get(order_pos.get(f.order_id)))
        return {"time": fmt_ist(from_db_time(f.timestamp), "%d %b %H:%M:%S") if f.timestamp else None,
                "order": f.order_id, "price": f.price, "size": f.size, "amount": gross,
                "amount (₹ est.)": inr(gross, rate) if gross is not None else MISSING,
                "fee": f.fee, "gst": f.gst, "mid at fill": f.mid_at_fill}

    orders = pd.DataFrame([_order_row(o) for o in order_rows])
    fills = pd.DataFrame([_fill_row(f) for f in fill_rows])

USD = st.column_config.NumberColumn(format="$%.2f")
AMT = st.column_config.NumberColumn("amount (USD)", format="$%.2f", help="Price × size × contract value (premium)")
cfgs = {"trades": {"amount used": st.column_config.NumberColumn("amount used (USD)", format="$%.2f",
                                                                help="Premium paid + entry fees (incl. GST)"),
                   "premium": USD, "fees": USD, "pnl": st.column_config.NumberColumn("pnl (USD)", format="$%.2f"),
                   "R": st.column_config.NumberColumn(format="%+.3f")},
        "orders": {"price": st.column_config.NumberColumn(format="%.2f"), "amount": AMT},
        "fills": {"price": st.column_config.NumberColumn(format="%.2f"), "amount": AMT,
                  "fee": st.column_config.NumberColumn(format="$%.4f"),
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
                 {"label": "Net P&L", "value": pnl(net), "tone": tone(net), "delta": pnl_inr(net, rate)},
                 {"label": "Amount used", "value": usd(h["amount used"].sum()),
                  "delta": f"≈ {inr(h['amount used'].sum(), rate)}", "help": "Premium + entry fees, closed trades"},
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
