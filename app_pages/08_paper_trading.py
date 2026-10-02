"""Paper Trading terminal: candles, order book, funding/OI, option chain ladder, payoff and a buy-only order ticket."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

from delta_intelligence.config.settings import get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist
from delta_intelligence.options.analytics import summarize_chain
from delta_intelligence.options.structures import leg_fee, long_call, long_put
from delta_intelligence.ui import charts, state
from delta_intelligence.ui.components import can_control, control_note, page_setup
from delta_intelligence.ui.format import money, pct, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Paper Trading")
s = get_settings()
rate = s.usdinr_rate
perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda p: f"{UNDERLYINGS[p].asset} ({p})")
u = UNDERLYINGS[perp]

# ---- price, candles ------------------------------------------------------------------------------------------------
tick = state.tickers().get(perp, {})
c = st.columns(5)
c[0].metric("Mark", usd(float(tick["mark_price"])) if tick.get("mark_price") else "-")
c[1].metric("Spot index", usd(float(tick["spot_price"])) if tick.get("spot_price") else "-")
c[2].metric("24h change", pct(float(tick.get("mark_change_24h") or 0)) if tick else "-")
c[3].metric("Funding (8h, units unverified)", tick.get("funding_rate", "-"))
c[4].metric("Open interest (USD)", usd(float(tick["oi_value_usd"]), 0) if tick.get("oi_value_usd") else "-")

df = state.candles(perp, "5m", 2)
if len(df):
    from delta_intelligence.features.feature_engine import compute_features

    f = compute_features(df, "5m")
    st.plotly_chart(charts.candles(df.tail(288), {"VWAP": f["vwap"].tail(288), "Supertrend": f["supertrend"].tail(288)},
                                   title=f"{perp} 5m (IST)"), width="stretch")
else:
    st.info("No candles available (offline or not cached).")

col_ob, col_fo = st.columns(2)
with col_ob:
    st.subheader("Order book (perp)")
    if not state.offline():
        try:
            ob = state.public().get_orderbook(perp, depth=8)
            bids = pd.DataFrame(ob.get("buy", []))[["price", "size"]] if ob.get("buy") else pd.DataFrame()
            asks = pd.DataFrame(ob.get("sell", []))[["price", "size"]] if ob.get("sell") else pd.DataFrame()
            b1, b2 = st.columns(2)
            b1.caption("Bids")
            b1.dataframe(bids, hide_index=True)
            b2.caption("Asks")
            b2.dataframe(asks, hide_index=True)
        except Exception as exc:
            st.caption(f"order book unavailable: {exc}")
with col_fo:
    st.subheader("Funding / open interest (hourly)")
    try:
        dm = state.data_manager()
        end = now_utc()
        fdf, _ = dm.get_series("FUNDING", perp, "1h", end - dt.timedelta(days=7), end) if not state.offline() else (pd.DataFrame(), {})
        odf, _ = dm.get_series("OI", perp, "1h", end - dt.timedelta(days=7), end) if not state.offline() else (pd.DataFrame(), {})
        if len(fdf):
            st.line_chart(fdf.set_index("timestamp")["close"].rename("funding"), height=150)
        if len(odf):
            st.line_chart(odf.set_index("timestamp")["close"].rename("open interest"), height=150)
    except Exception as exc:
        st.caption(f"funding/OI unavailable: {exc}")

# ---- option chain -------------------------------------------------------------------------------------------------
st.subheader("Option chain")
chain = state.chain()
spot = float(tick.get("spot_price") or 0) or None
expiries = chain.expiries(u.asset)
if not expiries:
    st.info("No live option chain (offline, or no listed options).")
    st.stop()
exp = st.selectbox("Expiry", expiries, format_func=lambda e: f"{fmt_ist(e, '%d %b %H:%M IST')} "
                                                           f"({(e - pd.Timestamp(now_utc())).total_seconds() / 3600:.1f} h)")
summ = next((x for x in summarize_chain(chain, u.asset, spot or 0) if x.expiry == exp), None)
if summ:
    m = st.columns(4)
    m[0].metric("ATM strike", f"{summ.atm_strike:,.0f}")
    m[1].metric("ATM IV", pct((summ.atm_iv or 0) * 100))
    m[2].metric("PCR (OI)", f"{summ.pcr_oi:.2f}" if summ.pcr_oi else "-")
    m[3].metric("PCR (volume)", f"{summ.pcr_volume:.2f}" if summ.pcr_volume else "-")
rows = []
for k in chain.strikes(u.asset, exp):
    cq = chain.quote(chain.symbol(u.asset, "C", k, exp) or "")
    pq = chain.quote(chain.symbol(u.asset, "P", k, exp) or "")
    rows.append({"C OI": cq.open_interest if cq else None, "C IV": cq.mark_iv if cq else None,
                 "C Δ": cq.delta if cq else None, "C bid": cq.bid if cq else None, "C ask": cq.ask if cq else None,
                 "strike": k, "P bid": pq.bid if pq else None, "P ask": pq.ask if pq else None,
                 "P Δ": pq.delta if pq else None, "P IV": pq.mark_iv if pq else None, "P OI": pq.open_interest if pq else None})
ladder = pd.DataFrame(rows)
if spot and len(ladder):
    ladder = ladder.iloc[(ladder["strike"] - spot).abs().argsort()[:21]].sort_values("strike")
st.dataframe(ladder.style.apply(lambda r: ["background-color: rgba(37,99,235,.12)" if summ and r["strike"] == summ.atm_strike
                                           else "" for _ in r], axis=1), hide_index=True, width="stretch")

# ---- ticket ----------------------------------------------------------------------------------------------------------
st.subheader("Order ticket (BUY only)")
t1, t2, t3 = st.columns(3)
kind = t1.radio("Option", ["Call", "Put"], horizontal=True)
strike = t2.selectbox("Strike", list(ladder["strike"]) if len(ladder) else [], index=len(ladder) // 2 if len(ladder) else 0)
contracts = t3.number_input("Contracts", min_value=1, value=10, step=1)
k_code = "C" if kind == "Call" else "P"
sym = chain.symbol(u.asset, k_code, strike, exp) if strike else None
q = chain.quote(sym) if sym else None
if q and q.ask:
    cv = q.contract_value or 0.001
    units = contracts * cv
    cost = q.ask * units
    c_ = s.costs
    fee = leg_fee(q.ask, q.spot or spot or 0, units, c_.fallback_taker_rate, c_.fallback_premium_cap_rate, c_.gst_rate)
    builder = long_call if k_code == "C" else long_put
    st_ = builder(u.asset, strike, exp.to_pydatetime(), contracts, cv, sym)
    be = st_.breakevens([q.ask])
    tt = st.columns(5)
    tt[0].metric("Premium (ask)", money(cost, rate))
    tt[1].metric("Fees + GST (entry)", usd(fee))
    tt[2].metric("Max loss", money(cost + fee, rate))
    tt[3].metric("Breakeven at expiry", f"{be[0]:,.2f}" if be else "-")
    tt[4].metric("Spread", pct((q.ask - q.bid) / q.mid * 100) if q.bid else "-")
    if spot:
        grid = np.linspace(spot * 0.9, spot * 1.1, 200)
        now_val = None
        if q.mark_iv:
            from delta_intelligence.options.pricing import bs_price, year_fraction

            tleft = year_fraction(max(0.0, (exp - pd.Timestamp(now_utc())).total_seconds()))
            now_val = bs_price(grid, strike, tleft, q.mark_iv, k_code) * units - cost
        st.plotly_chart(charts.payoff(grid, st_.pnl_at_expiry(grid, [q.ask]), now_val, spot, be), width="stretch")
    st.caption(f"{sym} · expiry {fmt_ist(exp)} · contract = {cv} {u.asset} · OI {q.open_interest} · "
               f"ask size {q.ask_size}. The order goes through the RISK ENGINE before the paper broker.")
    if st.button("Buy (paper)", disabled=not can_control()):
        from delta_intelligence.brokers.paper_broker import TradePlan
        from delta_intelligence.execution.account import AccountTracker
        from delta_intelligence.execution.engine import kill_switch_on
        from delta_intelligence.risk.risk_engine import LegQuote, ProposedTrade, evaluate_trade

        from delta_intelligence.options.analytics import iv_percentile

        hist = state.iv_history().get(u.asset)
        iv_pct = iv_percentile(summ.atm_iv, hist, pd.Timestamp(now_utc())) if summ and hist is not None else None
        b = state.paper_broker()
        snap = b.account_snapshot()
        acct = AccountTracker(s).account_state(snap, now_utc(), True, kill_switch_on())
        prop = ProposedTrade("Manual", u.asset, u.correlated_bucket, st_.name, st_.direction, cost + fee, cost + fee,
                             (exp - pd.Timestamp(now_utc())).total_seconds() / 3600, 0.0,
                             [LegQuote(sym, 1, q.bid, q.ask, q.open_interest, q.ask_size)],
                             iv_percentile=iv_pct)
        d = evaluate_trade(acct, prop)
        if not d.approved:
            st.error(f"Risk engine VETO: {d.reason}")
        else:
            r = b.open_structure(TradePlan("Manual", st_, risk_decision_id=d.decision_id, premium_stop_pct=None))
            (st.success if r.ok else st.error)(f"{'Bought' if r.ok else 'Not filled'}: {r.reason} {r.position_id or ''}")
    control_note()
else:
    st.caption("Pick a strike with a live ask.")
