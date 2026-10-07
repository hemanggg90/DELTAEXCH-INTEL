"""Dashboard pieces shared by Command Center, Market State and Strategy Intelligence: the IV percentile panel and the live
ranker's table. Read-only; everything shown carries its source, and a missing value is explained, never filled in."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from delta_intelligence.options import iv_live
from delta_intelligence.ui import state
from delta_intelligence.utils.timeutil import now_utc


def iv_panel(asset: str) -> None:
    """ATM IV, its 60-day percentile and exactly where the history comes from (or why there is none yet)."""
    chain = state.chain()
    cur_iv = None
    exps = chain.expiries(asset)
    if exps:
        spot = next((q.spot for q in chain.by_symbol.values() if q.underlying == asset and q.spot), None)
        near = next((e for e in exps if (e - pd.Timestamp(now_utc())).total_seconds() / 3600 >= 6), exps[-1])
        cur_iv = chain.atm_iv(asset, near, spot) if spot else None
    hist = state.iv_history().get(asset, pd.Series(dtype=float))
    info = state.iv_sources().get(asset, {"sources": [], "n_obs": 0})
    st_ = iv_live.iv_status(hist, info, cur_iv, pd.Timestamp(now_utc()))
    c = st.columns(3)
    c[0].metric(f"{asset} ATM IV", "-" if cur_iv is None else f"{cur_iv * 100:.1f}%")
    c[1].metric("IV percentile (60 d)", "-" if st_["percentile"] is None else f"{st_['percentile']:.0f}")
    c[2].metric("IV observations", f"{st_['n_obs']}/{iv_live.MIN_OBS} min")
    st.caption(st_["note"])


def ranker_note(decision) -> None:
    """The ranker's verdict stored on a Decision row (`Decision.ranking['ranker']`), if the engine had a ranker."""
    r = ((decision.ranking or {}).get("ranker")) if decision is not None else None
    if not r:
        return
    mode = r.get("mode")
    if r.get("selected"):
        st.success(f"Ranker ({mode}): selected **{r['selected']}**. {r.get('reason', '')}")
    else:
        st.info(f"Ranker ({mode}): **NO TRADE**. {r.get('reason', '')}")
    st.caption(f"{r.get('n_with_setup', 0)} of {r.get('n_candidates', 0)} strategies have a setup on this bar. "
               f"{r.get('evidence', '')}")
    table = pd.DataFrame(r.get("table") or [])
    if len(table):
        st.dataframe(table.drop(columns=["setup"], errors="ignore"), hide_index=True, width="stretch")
    if r.get("errors"):
        st.caption("Strategies that errored on this bar: " + ", ".join(r["errors"]))


def engine_ranker_line(eng) -> None:
    if eng is None:
        return
    stt = eng.status()
    mode = stt.get("ranker_mode", "off")
    desc = {"select": "the ranker picks the strategy; ONLY its pick may trade (paper)",
            "shadow": "the ranker ranks and displays, while the always-on variants trade as before",
            "off": "no ranker"}[mode]
    st.caption(f"Ranker mode **{mode}**: {desc}. {stt.get('ranker') or ''}")
