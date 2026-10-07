"""Logic for the "Option Data & Selection" page: recorder health, coverage/quality of the recorded chain, and readers for
the Phase 2 research output. Kept out of the page file so it can be tested without Streamlit. Read-only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from delta_intelligence.options import chain_quality as cq

ROOT = Path(__file__).resolve().parents[2]
PHASE2_JSON = ROOT / "research_lab" / "reports" / "phase2" / "results.json"
PHASE2_REPORT = ROOT / "research_lab" / "reports" / "phase2" / "PHASE2_REPORT.md"


def recorder_health(df: pd.DataFrame, now: pd.Timestamp | None = None, expected_every_sec: float = 300.0) -> dict:
    """Is the recorder alive and gap-free? `df` = recorded rows (any window). Never guesses: with no rows it says so."""
    now = now or pd.Timestamp.now(tz="UTC")
    if df.empty:
        return {"rows": 0, "alive": False, "last": None, "age_sec": None, "per_underlying": {}, "rows_last_hour": 0,
                "verdict": "No recorded rows. Run `python scripts/record_chain.py` (or the engine) to start collecting real quotes."}
    d = cq.prepare(df)
    last = d["taken_at"].max()
    age = (now - last).total_seconds()
    cutoff = now - pd.Timedelta(hours=1)
    per = {}
    for u, g in d.groupby("underlying"):
        t = pd.DatetimeIndex(sorted(g["taken_at"].unique()))
        gaps = [] if len(t) < 2 else [(a, b, float((b - a).total_seconds())) for a, b in zip(t[:-1], t[1:])
                                      if (b - a).total_seconds() > max(2.5 * expected_every_sec, 600.0)]
        per[u] = {"rows": int(len(g)), "snapshots": int(len(t)), "first": t[0], "last": t[-1], "gaps": gaps[-5:], "n_gaps": len(gaps)}
    alive = age <= 3 * expected_every_sec
    return {"rows": int(len(d)), "alive": alive, "last": last, "age_sec": age, "per_underlying": per,
            "rows_last_hour": int((d["taken_at"] >= cutoff).sum()),
            "verdict": ("Recording: the latest snapshot is recent." if alive else
                        f"STALLED: the latest snapshot is {age / 60:.0f} min old. Every minute not recorded is permanently missing real data.")}


def load_phase2() -> dict | None:
    if not PHASE2_JSON.exists():
        return None
    try:
        return json.loads(PHASE2_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heatmap_frame(res: dict, which: str = "holdout", min_n: int = 30) -> pd.DataFrame:
    """Rows = Phase 2 cells, columns = option policies, values = mean net R (None where fewer than min_n trades)."""
    rows = {}
    for key, c in res["cells"].items():
        row = {}
        for pid, m in c["policies"].items():
            x = m[which]
            row[pid] = x.get("net_r") if x.get("n", 0) >= min_n else None
        rows[key.replace("|", " ")] = row
    return pd.DataFrame(rows).T.reindex(columns=res["policies"]).astype(float)


def real_vs_model_table(res: dict) -> pd.DataFrame:
    rows = []
    for key, c in res["cells"].items():
        for a, n in c["real_notes"].items():
            rows.append({"cell": key.replace("|", " "), "asset": a, "signals in recorded window": n["signals_in_window"],
                         "REAL_ONLY trades": n["real_only_trades"], "fallback trades": n["fallback_trades"],
                         "labels": json.dumps(n["fallback_labels"]), "unpriced exits": n["unpriced_exits"]})
    return pd.DataFrame(rows)


def bucket_frame(res: dict, feature: str, cell: str | None = None) -> pd.DataFrame:
    """Selected-policy bucket table for one cell, or all cells pooled (weighted by n). MODELED unless pct_real says otherwise."""
    frames = []
    for key, c in res["cells"].items():
        if cell and key != cell:
            continue
        t = pd.DataFrame(c["buckets"].get(feature, []))
        if not t.empty:
            frames.append(t.assign(cell=key))
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames)
    if cell:
        return d.drop(columns=["cell"])
    g = d.groupby("bucket", sort=False)
    out = g.apply(lambda x: pd.Series({"n": x["n"].sum(), **{k: float(np.average(x[k].fillna(0), weights=x["n"])) for k in
                                                           ("net_r", "gross_r", "spread_r", "fees_r", "win_rate", "pct_real")}}),
                  include_groups=False)
    return out.reset_index()


def why_no_trade(res: dict, key: str) -> dict:
    """Everything the research says about why a configuration did not produce a tradeable result."""
    c = res["cells"][key]
    sel = c["policies"][c["selected"]]
    sk = dict(sorted(sel["skipped"].items(), key=lambda x: -x[1]))
    return {"status": c["status"], "reasons": c["reasons"], "skips": sk, "selected": c["selected"],
            "gates": c["gates"], "real_data": c["real_data"],
            "summary": ("NO TRADE: " + ("; ".join(c["reasons"]) if c["reasons"] else "no gate failed, but status is " + c["status"]))}
