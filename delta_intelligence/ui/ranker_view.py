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


LEADERBOARD_COLUMNS = ["rank", "strategy", "signal now", "score", "edge_r", "confidence", "samples", "eligible", "chosen", "note"]


def leaderboard_frame(rows: list[dict]) -> pd.DataFrame:
    """The ranker table as shown on the dashboard. Tolerant of OLDER saved decisions: an engine running older code stored only the
    strategies with a signal and had no `rank` / `selected` columns, so missing columns are derived or left blank, never a crash."""
    t = pd.DataFrame(rows)
    if t.empty:
        return pd.DataFrame(columns=LEADERBOARD_COLUMNS)
    if "strategy" not in t:
        return pd.DataFrame(columns=LEADERBOARD_COLUMNS)
    n = len(t)
    note = t["note"] if "note" in t else pd.Series([None] * n, index=t.index)
    setup = t["setup"] if "setup" in t else pd.Series([True] * n, index=t.index)  # old tables listed only signalling strategies
    selected = t["selected"] if "selected" in t else note.eq("SELECTED")
    out = pd.DataFrame({
        "rank": t["rank"] if "rank" in t else pd.Series(range(1, n + 1), index=t.index),
        "strategy": t["strategy"],
        "signal now": setup.map({True: "yes", False: "-"}).fillna("-"),
        "score": t["score"] if "score" in t else None, "edge_r": t["edge_r"] if "edge_r" in t else None,
        "confidence": t["confidence"] if "confidence" in t else None, "samples": t["samples"] if "samples" in t else None,
        "eligible": t["eligible"] if "eligible" in t else None,
        "chosen": selected.map({True: "◀ SELECTED", False: ""}).fillna(""), "note": note})
    return out[LEADERBOARD_COLUMNS]


def ranker_note(decision) -> None:
    """The ranker's verdict and the FULL leaderboard stored on a Decision (`Decision.ranking['ranker']`): every strategy is
    scored each bar, signalling or not; only a strategy with a signal right now can be traded."""
    r = ((decision.ranking or {}).get("ranker")) if decision is not None else None
    if not r:
        return
    mode = r.get("mode")
    if r.get("selected") and mode == "top":
        below = r.get("strict_selected") != r["selected"]
        (st.warning if below else st.success)(f"Ranker (top): trading **{r['selected']}**, the best-ranked signalling strategy. "
                                              f"{r.get('reason', '')}")
    elif r.get("selected"):
        st.success(f"Ranker ({mode}): selected **{r['selected']}**, the best-ranked strategy with a signal. {r.get('reason', '')}")
    else:
        st.info(f"Ranker ({mode}): **NO TRADE**. {r.get('reason', '')}")
    lead = r.get("leader")
    if lead:
        st.caption(f"Overall leader: **{lead['strategy']}** (score {lead['score']}), "
                   + ("it has a signal now." if lead["has_setup"] else "waiting for its signal."))
    st.caption(f"{r.get('n_with_setup', 0)} of {r.get('n_candidates', 0)} strategies have a signal on this bar. {r.get('evidence', '')}")
    view = leaderboard_frame(r.get("table") or [])
    if len(view):
        st.dataframe(view, hide_index=True, width="stretch", height=min(760, 38 + 35 * len(view)))
    if r.get("errors"):
        st.caption("Strategies that errored on this bar: " + ", ".join(r["errors"]))


def external_engine_message(owner: dict, age_sec: float | None, app_ranker_mode: str, my_version: str) -> tuple[str, str]:
    """(tone, text) for 'an engine is running in another process': WHO it is and what to do. Records written by engines that
    predate these fields simply lack them, which is itself reported (an older engine)."""
    from delta_intelligence.ui.format import duration
    from delta_intelligence.utils.timeutil import fmt_ist

    hb = "" if age_sec is None else f", heartbeat {duration(age_sec)} ago"
    if not owner or not owner.get("id"):
        return "info", (f"An engine is running in another process{hb}, but it left no details. Start is disabled here: two engines "
                        "would double-trade. Use the kill switch (Risk Control) to block new trades, or stop that process.")
    started = ""
    if owner.get("started_at"):
        try:
            import pandas as pd

            started = f", started {fmt_ist(pd.Timestamp(owner['started_at']).tz_localize('UTC') if pd.Timestamp(owner['started_at']).tzinfo is None else pd.Timestamp(owner['started_at']), '%d %b %H:%M IST')}"
        except Exception:
            started = ""
    if "version" not in owner:
        detail = "an OLDER engine (it recorded no mode, ranker or version), so it does not use the ranker leaderboard"
        tone = "warning"
    else:
        ranker = owner.get("ranker_mode") or "off"
        detail = f"mode {owner.get('mode', '?')}, ranker `{ranker}`, code `{owner.get('version', 'unknown')}`"
        tone = "info"
        if ranker in ("off", "shadow") and app_ranker_mode in ("top", "select"):
            tone, detail = "warning", detail + ". It is NOT letting the ranker choose trades"
        if owner.get("version") not in (None, "unknown") and my_version != "unknown" and owner["version"] != my_version:
            tone, detail = "warning", detail + f". This app runs code `{my_version}`: that engine runs different code"
    return tone, (f"An engine on `{owner['id']}`{started}{hb} is running: {detail}. Start is disabled here: two engines would "
                  "double-trade. If it is on your PC, stop it there (Ctrl+C in its terminal) or restart it with the latest code; "
                  "otherwise use the kill switch (Risk Control) to block new trades.")


def engine_ranker_line(eng, external_owner: dict | None = None) -> None:
    if external_owner is not None:
        mode = external_owner.get("ranker_mode") if "version" in external_owner else None
        st.caption("Another engine is running, so the lines here describe THIS app's configuration, not that engine. "
                   + (f"That engine runs ranker mode `{mode}`." if mode else "That engine is older or left no ranker details."))
    if eng is None:
        return
    stt = eng.status()
    mode = stt.get("ranker_mode", "off")
    desc = {"top": "the best-ranked signalling strategy trades (paper), trying the next one if rejected; may be below the strict "
                   "evidence bar",
            "select": "only the STRICT ranker's pick may trade (paper)",
            "shadow": "the ranker ranks and displays, while the always-on variants trade as before",
            "off": "no ranker"}[mode]
    st.caption(f"Ranker mode **{mode}**: {desc}. {stt.get('ranker') or ''}")
