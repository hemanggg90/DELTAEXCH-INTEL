"""Paper Trading terminal: quote, candles, order book, funding/OI, option chain ladder, payoff and a buy-only ticket."""
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
from delta_intelligence.ui.components import (can_control, control_note, empty_state, header_quote, highlight_rows,
                                              kpi_row, kv_grid, notice, page_setup, table)
from delta_intelligence.ui.format import money, num, pct, usd
from delta_intelligence.utils.timeutil import fmt_ist, now_utc

page_setup("Paper Trading", "Live Delta prices. Every order goes through the risk engine; BUY to open only.")
s = get_settings()
rate = s.usdinr_rate
perp = st.selectbox("Underlying", list(get_watchlist()), format_func=lambda p: f"{UNDERLYINGS[p].asset} ({p})")
u = UNDERLYINGS[perp]


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---- quote ---------------------------------------------------------------------------------------------------------
tick = state.tickers().get(perp, {})
mark = _f(tick.get("mark_price"))
if tick:
    funding = _f(tick.get("funding_rate"))
    header_quote(f"{u.asset} perpetual · {perp}", mark, _f(tick.get("mark_change_24h")),
                 [f"spot index {num(_f(tick.get('spot_price')))}", f"funding {num(funding, 4)} (8h, units UNVERIFIED)",
                  f"OI {usd(_f(tick.get('oi_value_usd')), 0)}", f"24h turnover {usd(_f(tick.get('turnover_usd')), 0)}"],
                 _f(tick.get("low")), _f(tick.get("high")), tags=[("mark price", "neutral")])
else:
    empty_state("No live quote", "Offline, or Delta's ticker endpoint didn't answer. The status bar shows API state.")

# ---- market sub-views ----------------------------------------------------------------------------------------------
view = st.segmented_control("View", ["Chart", "Order book", "Funding & OI"], default="Chart",
                            label_visibility="collapsed")
if view == "Order book":
    if state.offline():
        empty_state("Order book unavailable offline")
    else:
        try:
            ob = state.public().get_orderbook(perp, depth=8)
            b1, b2 = st.columns(2)
            cfg = {"price": st.column_config.NumberColumn(format="%.2f"), "size": st.column_config.NumberColumn()}
            with b1:
                st.caption("● Bids")
                table(pd.DataFrame(ob.get("buy", []))[["price", "size"]] if ob.get("buy") else None, cfg,
                      empty=("No bids", ""))
            with b2:
                st.caption("○ Asks")
                table(pd.DataFrame(ob.get("sell", []))[["price", "size"]] if ob.get("sell") else None, cfg,
                      empty=("No asks", ""))
        except Exception as exc:
            notice("warning", f"Order book unavailable: {exc}. It refreshes on the next rerun.")
elif view == "Funding & OI":
    try:
        dm = state.data_manager()
        end = now_utc()
        empty = (pd.DataFrame(), {})
        fdf, _ = dm.get_series("FUNDING", perp, "1h", end - dt.timedelta(days=7), end) if not state.offline() else empty
        odf, _ = dm.get_series("OI", perp, "1h", end - dt.timedelta(days=7), end) if not state.offline() else empty
        c1, c2 = st.columns(2)
        with c1, st.container(border=True):
            if len(fdf):
                charts.show(charts.line({"funding": fdf.set_index("timestamp")["close"]}, "Funding (hourly, 7d)",
                                        refs={"0": 0.0}))
            else:
                empty_state("No funding data", "Run scripts/fetch_candles.py with --series FUNDING.")
        with c2, st.container(border=True):
            if len(odf):
                charts.show(charts.line({"open interest": odf.set_index("timestamp")["close"]},
                                        "Open interest (hourly, 7d)"))
            else:
                empty_state("No open-interest data", "Run scripts/fetch_candles.py with --series OI.")
    except Exception as exc:
        notice("warning", f"Funding/OI unavailable: {exc}")
else:
    df = state.candles(perp, "5m", 2)
    if len(df):
        from delta_intelligence.features.feature_engine import compute_features

        f = compute_features(df, "5m")
        with st.container(border=True):
            charts.show(charts.candles(df.tail(288), {"VWAP": f["vwap"].tail(288),
                                                      "Supertrend": f["supertrend"].tail(288)},
                                       title=f"{perp} · 5m · IST"))
    else:
        empty_state("No candles", "Offline or not cached: run scripts/fetch_candles.py.")

# ---- option chain -------------------------------------------------------------------------------------------------
st.subheader("Option chain")
chain = state.chain()
spot = _f(tick.get("spot_price"))
expiries = chain.expiries(u.asset)
if not expiries:
    empty_state("No live option chain", "Offline, or this underlying has no listed options right now.")
    st.stop()
exp = st.selectbox("Expiry", expiries, format_func=lambda e: f"{fmt_ist(e, '%d %b %H:%M IST')} "
                                                           f"({(e - pd.Timestamp(now_utc())).total_seconds() / 3600:.1f} h)")
summ = next((x for x in summarize_chain(chain, u.asset, spot or 0) if x.expiry == exp), None)
if summ:
    kpi_row([{"label": "ATM strike", "value": num(summ.atm_strike, 0)},
             {"label": "ATM IV", "value": pct((summ.atm_iv or 0) * 100) if summ.atm_iv else "–"},
             {"label": "PCR (OI)", "value": num(summ.pcr_oi)},
             {"label": "PCR (volume)", "value": num(summ.pcr_volume)}])
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
    ladder = ladder.iloc[(ladder["strike"] - spot).abs().argsort()[:21]].sort_values("strike").reset_index(drop=True)
ladder_cfg = {**{c: st.column_config.NumberColumn(format="%.2f") for c in ("C bid", "C ask", "P bid", "P ask")},
              **{c: st.column_config.NumberColumn(format="%.3f") for c in ("C Δ", "P Δ", "C IV", "P IV")},
              **{c: st.column_config.NumberColumn(format="%d") for c in ("C OI", "P OI")},
              "strike": st.column_config.NumberColumn(format="%.0f", pinned=True)}
st.caption("Calls left, puts right; ATM row highlighted (◎ ATM in the strike list below).")
table(highlight_rows(ladder, [summ is not None and k == summ.atm_strike for k in ladder["strike"]])
      if len(ladder) else None, ladder_cfg, height=36 * (len(ladder) + 1) + 4,
      empty=("No strikes", "No quotes for this expiry."))

# ---- ticket ----------------------------------------------------------------------------------------------------------
st.subheader("Order ticket · BUY only")
with st.container(border=True):
    t1, t2, t3 = st.columns(3)
    kind = t1.segmented_control("Option", ["Call", "Put"], default="Call") or "Call"
    strikes = list(ladder["strike"]) if len(ladder) else []
    strike = t2.selectbox("Strike", strikes, index=len(strikes) // 2 if strikes else 0,
                          format_func=lambda k: f"{k:,.0f}" + (" ◎ ATM" if summ and k == summ.atm_strike else ""))
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
        c_left, c_right = st.columns([2, 3])
        with c_left:
            kv_grid([("Symbol", sym), ("Expiry", fmt_ist(exp, "%d %b %H:%M IST")),
                     ("Premium (ask)", money(cost, rate)), ("Fees + GST (entry)", usd(fee)),
                     ("Amount used = max loss", money(cost + fee, rate)), ("Breakeven at expiry", num(be[0]) if be else "–"),
                     ("Spread", pct((q.ask - q.bid) / q.mid * 100) if q.bid else "–"),
                     ("Contract", f"{cv} {u.asset}"), ("Open interest", num(q.open_interest, 0)),
                     ("Ask size", num(q.ask_size, 0))])
            st.caption("The order goes through the RISK ENGINE before the paper broker.")
            buy = st.button("Buy (paper)", type="primary", disabled=not can_control(), width="stretch")
        with c_right:
            if spot:
                grid = np.linspace(spot * 0.9, spot * 1.1, 200)
                now_val = None
                if q.mark_iv:
                    from delta_intelligence.options.pricing import bs_price, year_fraction

                    tleft = year_fraction(max(0.0, (exp - pd.Timestamp(now_utc())).total_seconds()))
                    now_val = bs_price(grid, strike, tleft, q.mark_iv, k_code) * units - cost
                charts.show(charts.payoff(grid, st_.pnl_at_expiry(grid, [q.ask]), now_val, spot, be, "Payoff"))
        if buy:
            from delta_intelligence.brokers.paper_broker import TradePlan
            from delta_intelligence.execution.account import AccountTracker
            from delta_intelligence.execution.engine import kill_switch_on
            from delta_intelligence.options.analytics import iv_percentile
            from delta_intelligence.risk.risk_engine import LegQuote, ProposedTrade, evaluate_trade

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
                notice("critical", f"Risk engine VETO: {d.reason}. Nothing was sent. Pick another strike/size, or "
                                   "see the limits on Risk Control.")
            else:
                r = b.open_structure(TradePlan("Manual", st_, risk_decision_id=d.decision_id, premium_stop_pct=None))
                notice("good" if r.ok else "critical",
                       f"{'Bought' if r.ok else 'Not filled'}: {r.reason} {r.position_id or ''}")
        control_note()
    else:
        empty_state("No live ask for this strike", "Pick a strike that has an ask in the ladder above.")
