"""The whole staged pipeline (baseline -> stability -> control -> classification -> report) on a synthetic option market.
Proves the plumbing, not any market fact: synthetic prices have no edge and the period precedes the frozen holdout."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.backtesting.option_market import OptionMarketModel
from lab import evaluate as E
from lab import pipeline as PL
from lab import runner
from lab.frame import build_lab_frame
from lab.report import render, to_json
from synth import iv_hourly, make_inputs


@pytest.fixture(scope="module")
def fake_asset():
    o, aux = make_inputs(days=40, seed=5)
    iv = iv_hourly(o)
    frames = {tf: build_lab_frame(o, aux, tf, iv, with_regime=True) for tf in ("5m", "15m")}
    for tf in frames:
        frames[tf] = frames[tf][frames[tf]["timestamp"] >= pd.Timestamp("2026-01-08", tz="UTC")].reset_index(drop=True)
    h = pd.date_range("2025-12-31T00:00Z", periods=24 * 70, freq="1h")
    hourly = pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"),
                           **{f"atm_iv_{b}": 0.5 for b in ("0_6h", "6_30h", "30_54h", "54_168h", "168_720h")}})
    expiries = list(pd.date_range("2026-01-07T12:00Z", periods=60, freq="D"))
    strikes = np.arange(20000, 140001, 500, dtype=float)
    market = OptionMarketModel("BTC", aux.index, hourly, listed_strikes={e: strikes for e in expiries})
    return runner.AssetData("BTC", "BTCUSD", frames, market, {"asset": "BTC", "perp": "BTCUSD", "options": {
        "expiries": 60, "first_expiry": str(expiries[0]), "last_expiry": str(expiries[-1])}, "aux_errors": {}})


@pytest.fixture
def patched(monkeypatch, fake_asset):
    monkeypatch.setattr(runner, "load_asset", lambda perp, since, tfs: fake_asset)


IDS = ["S2-n20", "S6-base", "S8-20-50", "LEGACY-ST-tight-ATM"]


def test_phase_a_runs_directional_straddle_and_legacy_cells(patched) -> None:
    out = runner.phase_a("BTCUSD", "2026-01-08", ("5m", "15m"), IDS)
    assert out["asset"] == "BTC"
    keys = set(out["cells"])
    assert {("S2-n20", "5m"), ("S2-n20", "15m"), ("S6-base", "5m"), ("LEGACY-ST-tight-ATM", "5m")} <= keys
    assert ("LEGACY-ST-tight-ATM", "15m") not in keys  # the legacy variants are 5m only, as in the project
    c = out["cells"][("S2-n20", "5m")]
    assert c["n_setups"] > 0 and c["trades"], c["skipped"]
    assert all(hasattr(t, "regime") for t in c["trades"])  # each trade is tagged with its regime
    straddles = out["cells"][("S6-base", "5m")]["trades"]
    assert all(t.structure == "LONG_STRADDLE" and len(t.entry_fills) == 2 for t in straddles)


def test_phase_c_and_d_return_the_stability_and_control_structures(patched) -> None:
    jobs = [("S2-n20", "5m"), ("LEGACY-ST-tight-ITM1", "5m"), ("S6-base", "15m")]
    c = runner.phase_c("BTCUSD", "2026-01-08", ("5m", "15m"), jobs)["cells"]
    rec = c[("S2-n20", "5m")]
    assert set(rec["perturb"]) == {f"{k}x{f}" for k in ("n", "rr", "stop_atr") for f in (0.8, 1.2)}
    assert {"ATM_040_060@2.5", "ATM_040_060@4.0", "ATM_030_060@2.5", "ITM1_050_070@2.5"} == set(rec["buckets"])
    assert rec["frictionless"][1] >= 0 and all(len(v["holdout"]) == 2 for v in rec["buckets"].values())
    assert set(c[("LEGACY-ST-tight-ITM1", "5m")]["perturb"]) == {"target_atr_multx0.8", "target_atr_multx1.2"}
    d = runner.phase_d("BTCUSD", "2026-01-08", ("5m", "15m"), jobs, runs=4)["cells"]
    assert all(len(series) == 4 for series in d.values())
    assert d == runner.phase_d("BTCUSD", "2026-01-08", ("5m", "15m"), jobs, runs=4)["cells"]  # seeded: reproducible


def test_frictionless_beats_net_when_costs_exist(patched) -> None:
    jobs = [("S2-n20", "5m")]
    rec = runner.phase_c("BTCUSD", "2026-01-08", ("5m",), jobs)["cells"][("S2-n20", "5m")]
    a = runner.phase_a("BTCUSD", "2026-01-08", ("5m",), ["S2-n20"])["cells"][("S2-n20", "5m")]
    s_fr, n_fr = rec["frictionless"]
    net = np.mean([t.net_r for t in a["trades"]])
    assert n_fr >= len(a["trades"]) and s_fr / n_fr > net  # no spread/fees/gate -> better, and the gate no longer skips


def test_full_pipeline_writes_a_report_with_every_status_honest(patched, tmp_path) -> None:
    logs = []
    res = PL.run_pipeline(["BTC"], ("5m", "15m"), "2026-01-08", tmp_path, workers=1, control_runs=3, variant_ids=IDS,
                          log=logs.append)
    assert not res["errors"] and res["cells"] and set(res["verdicts"]) == set(res["cells"])
    # synthetic data has no holdout trades and no edge: nothing may be ACCEPTED
    assert all(v["status"] != E.ACCEPTED for v in res["verdicts"].values())
    assert all(v["status"] in (E.DATA_INSUFFICIENT, E.REJECTED) for v in res["verdicts"].values())
    md = render(res)
    assert "ACCEPTED" in md and "DATA-INSUFFICIENT" in md and "S2-n20" in md and "LEGACY-ST-tight-ATM" in md
    assert "Supertrend: previous result" in md and "Straddle economics" in md
    js = to_json(res)
    json.dumps(js)  # serialisable
    assert js["cells"]["S2-n20|5m"]["status"] in (E.DATA_INSUFFICIENT, E.REJECTED)
    assert any("phase A" in x for x in logs) and list(tmp_path.glob("phase_a_*.pkl"))
    again = PL.run_pipeline(["BTC"], ("5m", "15m"), "2026-01-08", tmp_path, reuse=True, workers=1, control_runs=3,
                            variant_ids=IDS, log=logs.append)
    assert any("reusing" in x for x in logs)
    assert {k: v["status"] for k, v in again["verdicts"].items()} == {k: v["status"] for k, v in res["verdicts"].items()}


def test_a_failing_asset_is_reported_not_hidden(monkeypatch, tmp_path, fake_asset) -> None:
    def loader(perp, since, tfs):
        if perp == "XAUTUSD":
            raise FileNotFoundError("no IV store for XAUT")
        return fake_asset

    monkeypatch.setattr(runner, "load_asset", loader)
    res = PL.run_pipeline(["BTC", "XAUT"], ("5m",), "2026-01-08", tmp_path, workers=1, control_runs=2,
                          variant_ids=["S8-20-50"], log=lambda m: None)
    assert "XAUT" in res["errors"] and "no IV store" in res["errors"]["XAUT"]
    assert "XAUT" not in next(iter(res["cells"].values()))["assets"]
    assert "Run errors" in render(res)
