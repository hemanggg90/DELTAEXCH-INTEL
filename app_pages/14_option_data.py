"""Option Data & Selection: how much REAL option-chain data exists and how good it is, whether the recorder is alive, and what
the Phase 2 research says about option-selection policies (strategy x policy heatmap, real vs modelled, IV vs realised vol,
spread cost, DTE, delta, and "why no trade?").

Read-only. Real recorded data (REAL_RECORDED) and modelled prices are labelled everywhere and never mixed. NO TRADE is a
valid, preferred answer whenever the evidence is insufficient.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from delta_intelligence.options import chain_quality as cq
from delta_intelligence.ui import option_data as od
from delta_intelligence.ui import state
from delta_intelligence.ui.components import page_setup

page_setup("Option Data & Selection")
st.caption("REAL = recorded Delta option quotes. MODELED = Black-Scholes at the as-of IV inferred from real option trades, with a "
           "modelled spread. A real quote is never invented and a modelled price is never presented as real.")


@st.cache_data(ttl=60, show_spinner=False)
def recorded(days: int):
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    raw = cq.load_snapshots(since=since)
    return cq.assess(raw)


days = st.sidebar.slider("Recorded data window (days)", 1, 30, 7)
assessed, qrep = recorded(days)
res = od.load_phase2()

tabs = st.tabs(["Coverage", "Recorder health", "Data quality", "Strategy x policy", "Real vs modeled", "IV vs realised vol",
                "Spread cost", "DTE", "Delta", "Why no trade?"])

with tabs[0]:
    st.subheader("How much REAL option data exists")
    if assessed.empty:
        st.warning("No recorded option-chain rows in this window. Start `python scripts/record_chain.py`, or run the engine.")
    else:
        cov = cq.coverage(assessed)
        k = st.columns(4)
        k[0].metric("Rows", f"{len(assessed):,}")
        k[1].metric("Snapshots", qrep.n_snapshots)
        k[2].metric("REAL_QUOTE rows", f"{qrep.tier_counts.get('REAL_QUOTE', 0):,}")
        k[3].metric("Mark-only / unusable", f"{qrep.tier_counts.get('REAL_MARK_ONLY', 0):,} / {qrep.tier_counts.get('UNUSABLE', 0):,}")
        asset = st.selectbox("Asset", sorted(assessed["underlying"].unique()))
        sub = assessed[assessed["underlying"] == asset]
        c2 = cq.coverage(sub)
        for key, title in (("by_day", "By date"), ("by_expiry", "By expiry"), ("by_kind", "By option type"),
                           ("by_region", "By strike region"), ("by_dte", "By DTE"), ("by_timeframe", "By timeframe")):
            st.markdown(f"**{title}**")
            st.dataframe(c2[key], hide_index=True, width="stretch")
        del cov
    if res:
        st.caption(f"Phase 2 research (protocol `{res['protocol2_hash'][:12]}`) saw {res['real']['snapshot_days']} day(s) of snapshots. "
                   "REAL-DATA-VALIDATED needs at least 14.")

with tabs[1]:
    st.subheader("Live recorder health")
    h = od.recorder_health(assessed.drop(columns=[c for c in assessed.columns if c in cq.FLAGS], errors="ignore")
                           if not assessed.empty else assessed)
    (st.success if h["alive"] else st.error)(h["verdict"])
    k = st.columns(3)
    k[0].metric("Rows in window", f"{h['rows']:,}")
    k[1].metric("Rows, last hour", f"{h['rows_last_hour']:,}")
    k[2].metric("Latest snapshot age", "-" if h["age_sec"] is None else f"{h['age_sec'] / 60:.1f} min")
    for u, p in h["per_underlying"].items():
        st.write(f"**{u}**: {p['rows']:,} rows, {p['snapshots']} snapshots, {p['n_gaps']} gap(s) between snapshots "
                 f"({str(p['first'])[:16]} to {str(p['last'])[:16]} UTC)")
        for a, b, s in p["gaps"]:
            st.caption(f"gap: {str(a)[:16]} to {str(b)[:16]} ({s / 60:.0f} min), permanently missing")
    st.caption("Start: `python scripts/record_chain.py` (public data only, no keys), or run the engine, which records every 5 minutes. "
               "The PC must stay awake.")

with tabs[2]:
    st.subheader("Historical data quality")
    if assessed.empty:
        st.info("Nothing recorded yet.")
    else:
        fl = pd.DataFrame([{"flag": f, "rows": qrep.flag_counts[f], "% of rows": round(qrep.pct(f), 2)} for f in cq.FLAGS])
        st.dataframe(fl, hide_index=True, width="stretch")
        st.write(f"Snapshot gaps: {len(qrep.gaps)}; median interval (s): {qrep.median_interval_sec}")
        for n in qrep.notes:
            st.warning(n)
        st.caption("Tiers: REAL_QUOTE (usable two-sided fresh quote), REAL_MARK_ONLY (a mark is not a bid or ask), UNUSABLE.")

if res is None:
    for t in tabs[3:]:
        with t:
            st.info("No Phase 2 research output yet. Run `python research_lab/run_phase2.py` (see research_lab/README.md).")
else:
    with tabs[3]:
        st.subheader("Strategy x option-selection heatmap (MODELED prices)")
        which = st.radio("Period", ["holdout", "discovery"], horizontal=True)
        hm = od.heatmap_frame(res, which)
        st.caption("Mean net R per cell and option policy (blank = fewer than 30 trades or not applicable). Colours are for orientation "
                   "only: a policy chosen on the holdout would be overfitted, so the research selects on discovery only.")
        fig = go.Figure(go.Heatmap(z=hm.values, x=list(hm.columns), y=list(hm.index), zmid=0, colorscale="RdYlGn",
                                   colorbar=dict(title="net R"), hoverongaps=False))
        fig.update_layout(height=max(360, 22 * len(hm) + 120), margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        st.dataframe(pd.DataFrame([{"cell": k.replace("|", " "), "selected on discovery": c["selected"], "status": c["status"]}
                                   for k, c in res["cells"].items()]), hide_index=True, width="stretch")

    with tabs[4]:
        st.subheader("Real versus modeled")
        mvr = res["real"]["model_vs_real"]
        got = {a: v for a, v in mvr.items() if v and v.get("n")}
        if not got:
            st.warning("DATA-INSUFFICIENT: no recorded quote could be compared with the model.")
        for a, v in got.items():
            st.markdown(f"**{a}: model price vs real recorded quote** ({v['n']} quotes; positive error = model richer than the quote)")
            st.dataframe(pd.DataFrame([v["overall"]]), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(v["by_dte"]), hide_index=True, width="stretch")
        t = od.real_vs_model_table(res)
        st.markdown("**Trades priced from real quotes**")
        st.dataframe(t, hide_index=True, width="stretch") if len(t) else st.info("No signal fell inside the recorded window.")

    cells = list(res["cells"])
    for tab, feat, title, note in ((tabs[5], "iv_rv", "IV versus realised volatility", "IV / RV at entry; > 1 means options were richer than recent movement."),
                                   (tabs[6], "spread_pct", "Spread cost", "Entry spread as % of mid; the spread and fees columns are R lost per trade."),
                                   (tabs[7], "dte_days", "DTE performance", "Days to expiry at entry."),
                                   (tabs[8], "abs_delta", "Delta performance", "|delta| of the chosen contract.")):
        with tab:
            st.subheader(title + " (MODELED unless pct_real > 0)")
            st.caption(note)
            sel = st.selectbox("Cell", ["all cells pooled"] + cells, key="cell_" + feat)
            f = od.bucket_frame(res, feat, None if sel.startswith("all") else sel)
            st.dataframe(f, hide_index=True, width="stretch") if len(f) else st.info("No trades in this view.")

    with tabs[9]:
        st.subheader("Why no trade?")
        key = st.selectbox("Configuration", cells, key="why")
        w = od.why_no_trade(res, key)
        (st.success if w["status"] in ("ACCEPTED", "REAL-DATA-VALIDATED") else st.warning)(f"{key.replace('|', ' ')}: {w['status']}. {w['summary']}")
        st.write(f"Option policy selected on discovery: **{w['selected']}**")
        st.markdown("**Why signals did not become trades (skip counts, selected policy):**")
        st.dataframe(pd.DataFrame([{"reason": k, "count": v} for k, v in w["skips"].items()]), hide_index=True, width="stretch")
        st.markdown("**Gates:**")
        st.dataframe(pd.DataFrame([{"gate": g, "result": "pass" if d["ok"] else "FAIL" if d["ok"] is False else "not run", "detail": d["detail"]}
                                   for g, d in w["gates"].items()]), hide_index=True, width="stretch")
        st.caption(f"Real-data bar: {w['real_data'].get('detail', '')}")
        if not state.offline():
            from delta_intelligence.database import db
            from delta_intelligence.database.models import Decision

            with db.get_session() as ses:
                last = ses.query(Decision).order_by(Decision.id.desc()).limit(10).all()
            if last:
                st.markdown("**Latest live engine decisions (no-trade reasons):**")
                st.dataframe(pd.DataFrame([{"time": str(d.created_at)[:19], "action": getattr(d, "action", ""),
                                            "no trade reason": d.no_trade_reason} for d in last]), hide_index=True, width="stretch")
