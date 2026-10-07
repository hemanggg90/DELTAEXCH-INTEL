"""Gates, status classification, random control, metrics, pipeline merging and the report. Pure logic, no data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.strategies.base import Setup
from helpers import make_trade
from lab import config as C
from lab import controls, evaluate as E, metrics as M, pipeline as PL
from lab.library import VARIANTS, strategies_in_order
from lab.report import render, to_json


def trades(n: int, r: float, start="2026-06-01", step_h=4, jitter=None):
    base = pd.Timestamp(start, tz="UTC")
    out = []
    for i in range(n):
        x = r if jitter is None else r + jitter[i % len(jitter)]
        out.append(make_trade(x, str(base + pd.Timedelta(hours=step_h * i)), entry_idx=i * 20))
    return out


def asset(tr):
    return {"trades": tr, "n_setups": len(tr), "skipped": {}}


def good_cell(**over):
    """Three assets, plenty of trades, positive everywhere, with stability results attached."""
    jit = [0.3, -0.2, 0.1, -0.1]
    cell = {"assets": {"BTC": asset(trades(250, 0.15, jitter=jit)), "ETH": asset(trades(250, 0.12, jitter=jit)),
                       "XAUT": asset(trades(10, 0.1))}, "n_setups": 700,
            "perturb": {"nx0.8": {"BTC": (30.0, 200)}, "nx1.2": {"BTC": (25.0, 200)}},
            "buckets": {"ATM_040_060@2.5": {"holdout": {"BTC": (5.0, 100)}, "all": {}},
                        "ATM_030_060@2.5": {"holdout": {"BTC": (4.0, 100)}, "all": {}},
                        "ITM1_050_070@2.5": {"holdout": {"BTC": (-1.0, 100)}, "all": {}},
                        "ATM_040_060@4.0": {"holdout": {"BTC": (-9.0, 100)}, "all": {}}},
            "control": {"BTC": [(0.0, 100)] * 300, "ETH": [(0.0, 100)] * 300}}
    cell.update(over)
    return cell


# ---- statuses ------------------------------------------------------------------------------------------------------
def test_accepted_only_when_every_gate_passes() -> None:
    v = E.classify(good_cell(), n_cells=2)
    assert v["status"] == E.ACCEPTED and not v["reasons"]
    assert all(g["ok"] for g in v["gates"].values())


def test_data_insufficient_is_not_a_lowered_threshold() -> None:
    cell = {"assets": {"BTC": asset(trades(120, 0.5)), "ETH": asset(trades(60, 0.5))}, "n_setups": 400}
    v = E.classify(cell, 1)
    assert v["status"] == E.DATA_INSUFFICIENT and "180 trades" in v["reasons"][0] and "400 setups" in v["reasons"][0]
    # 200+ trades but the frozen holdout has too few: still data-insufficient
    early = {"assets": {"BTC": asset(trades(230, 0.5, start="2025-12-01", step_h=2))}, "n_setups": 230}
    assert E.classify(early, 1)["status"] == E.DATA_INSUFFICIENT


def test_rejected_when_holdout_is_not_positive_after_costs() -> None:
    cell = good_cell()
    cell["assets"]["BTC"] = asset(trades(250, -0.05))
    cell["assets"]["ETH"] = asset(trades(250, -0.02))
    v = E.classify(cell, 1)
    assert v["status"] == E.REJECTED and "not positive" in v["reasons"][0]


def test_experimental_when_holdout_is_positive_but_another_gate_fails() -> None:
    bad_perturb = good_cell(perturb={"nx0.8": {"BTC": (-5.0, 200)}, "nx1.2": {"BTC": (25.0, 200)}})
    v = E.classify(bad_perturb, 1)
    assert v["status"] == E.EXPERIMENTAL and any(r.startswith("perturbation") for r in v["reasons"])
    one_asset = good_cell()
    one_asset["assets"]["ETH"] = asset(trades(250, -0.01))
    v = E.classify(one_asset, 1)
    assert v["status"] == E.EXPERIMENTAL and any(r.startswith("assets") for r in v["reasons"])
    few_buckets = good_cell(buckets={"ATM_040_060@2.5": {"holdout": {"BTC": (5.0, 100)}, "all": {}},
                                     "ATM_030_060@2.5": {"holdout": {"BTC": (-4.0, 100)}, "all": {}},
                                     "ITM1_050_070@2.5": {"holdout": {"BTC": (-1.0, 100)}, "all": {}}})
    assert E.classify(few_buckets, 1)["gates"]["buckets"]["ok"] is False


def test_a_profit_that_random_entries_match_is_not_accepted() -> None:
    cell = good_cell(control={"BTC": [(30.0, 100)] * 300, "ETH": [(30.0, 100)] * 300})  # null mean +0.3: beats the cell
    v = E.classify(cell, 1)
    assert v["status"] == E.EXPERIMENTAL and v["gates"]["control"]["ok"] is False


def test_control_pvalue_is_bonferroni_adjusted_over_the_strategys_cells() -> None:
    cell = good_cell(control={"BTC": [(-5.0, 100)] * 299 + [(99.0, 100)], "ETH": [(0.0, 100)] * 300})
    one = E.gate_results(cell, 1)["control"]
    many = E.gate_results(cell, 30)["control"]
    assert one[0] is True and many[0] is False  # one lucky random run in 300 is fine alone, not across 30 cells
    assert E.control_pvalue(0.1, [0.0] * 99) == pytest.approx(1 / 100)
    assert E.control_pvalue(0.1, [0.5] * 9) == 1.0


def test_unrun_gates_are_reported_as_not_run_never_assumed() -> None:
    cell = good_cell()
    del cell["control"]
    v = E.classify(cell, 1)
    assert v["status"] == E.EXPERIMENTAL and "control: not run" in v["reasons"]
    assert E.needs_control(cell, 1) and not E.needs_control({**cell, "perturb": {"a": {"BTC": (-1.0, 100)}}}, 1)


def test_stability_runs_are_only_triggered_for_promising_cells() -> None:
    assert E.needs_stability_runs(good_cell())
    neg = {"assets": {"BTC": asset(trades(300, -0.1))}, "n_setups": 300}
    assert not E.needs_stability_runs(neg)
    small = {"assets": {"BTC": asset(trades(50, 0.5))}, "n_setups": 50}
    assert not E.needs_stability_runs(small)


def test_best_status_orders_accepted_first_and_empty_is_insufficient() -> None:
    assert E.best_status([E.REJECTED, E.EXPERIMENTAL, E.DATA_INSUFFICIENT]) == E.EXPERIMENTAL
    assert E.best_status([E.REJECTED, E.DATA_INSUFFICIENT]) == E.REJECTED
    assert E.best_status([]) == E.DATA_INSUFFICIENT


# ---- perturbation -----------------------------------------------------------------------------------------------------
def test_perturbations_are_plus_minus_20_percent_with_integer_handling() -> None:
    p = dict(E.perturbed_configs(C.DonchianConfig(n=20)))
    assert p["nx0.8"].n == 16 and p["nx1.2"].n == 24 and p["rrx0.8"].rr == pytest.approx(1.6)
    assert p["stop_atrx1.2"].stop_atr == pytest.approx(1.8)
    small = dict(E.perturbed_configs(C.SweepMSSConfig(swing_n=2)))
    assert small["swing_nx0.8"] is None  # 2*0.8 rounds to 2 -> moved to 1, which the config rejects: reported, not clipped
    assert small["swing_nx1.2"].swing_n == 3
    pct = dict(E.perturbed_configs(C.StraddleConfig(rv_pct_max=0.9)))
    assert pct["rv_pct_maxx1.2"] is None and pct["rv_pct_maxx0.8"].rv_pct_max == pytest.approx(0.72)


def test_every_variant_has_valid_key_parameters() -> None:
    for v in VARIANTS:
        keys = v.config.key_params
        assert keys and all(hasattr(v.config, k) for k in keys), v.id
        got = E.perturbed_configs(v.config)
        assert len(got) == 2 * len(keys)
        assert any(c is not None for _, c in got), v.id


# ---- controls -------------------------------------------------------------------------------------------------------
def test_random_direction_control_keeps_times_and_valid_geometry() -> None:
    base = [Setup(pd.Timestamp("2026-03-01", tz="UTC") + pd.Timedelta(hours=i), "LONG" if i % 2 else "SHORT", 100.0,
                  98.0 if i % 2 else 102.0, 104.0 if i % 2 else 96.0, "x") for i in range(400)]
    out = controls.randomize_directions(base, np.random.default_rng(1))
    assert [s.timestamp for s in out] == [s.timestamp for s in base]
    flipped = [(a, b) for a, b in zip(base, out) if a.direction != b.direction]
    assert 120 < len(flipped) < 280  # about half
    for s in out:
        if s.direction == "LONG":
            assert s.stop_price < s.entry_price < s.target_price
        else:
            assert s.target_price < s.entry_price < s.stop_price
    again = controls.randomize_directions(base, np.random.default_rng(1))
    assert [s.direction for s in again] == [s.direction for s in out]  # seeded: reproducible


def test_straddle_control_draws_random_entry_times() -> None:
    from helpers import mini_frame

    f = mini_frame(500)
    s = __import__("lab.strategies", fromlist=["x"]).LongStraddleExpansion(C.StraddleConfig())
    out = controls.random_entry_setups(s, f, 30, np.random.default_rng(3))
    assert len(out) == 30 and all(x.direction == "VOL" and x.meta["expected_abs_move"] > 0 for x in out)
    assert controls.random_entry_setups(s, f, 0, np.random.default_rng(3)) == []


# ---- metrics ---------------------------------------------------------------------------------------------------------
def test_trade_metrics_values() -> None:
    r = [1.0, -0.5, -0.5, 2.0, -1.0, -1.0, -1.0]
    tr = [make_trade(x, str(pd.Timestamp("2026-03-01", tz="UTC") + pd.Timedelta(days=i)), hold=12) for i, x in enumerate(r)]
    m = M.trade_metrics(tr, window_bars=1000)
    assert m["n"] == 7 and m["win_rate"] == pytest.approx(2 / 7) and m["expectancy_r"] == pytest.approx(np.mean(r))
    assert m["avg_win_r"] == pytest.approx(1.5) and m["avg_loss_r"] == pytest.approx(-0.8)
    assert m["profit_factor"] == pytest.approx(30.0 / 40.0)  # winners 3.0, losers 4.0 (x max_loss 10)
    assert m["max_consecutive_losses"] == 3 and m["max_drawdown_r"] == pytest.approx(-3.0)
    assert m["exposure"] == pytest.approx(7 * 12 / 1000) and m["avg_hold_hours"] == pytest.approx(1.0)
    assert m["sharpe_per_trade"] is None  # fewer than 50 trades: not statistically meaningful


def test_sharpe_and_sortino_appear_with_enough_trades() -> None:
    m = M.trade_metrics(trades(120, 0.1, jitter=[0.5, -0.4, 0.2, -0.3]))
    assert m["sharpe_per_trade"] is not None and m["sortino_per_trade"] is not None
    assert M.trade_metrics([]) == {"n": 0}


def test_regime_breakdown_and_folds() -> None:
    tr = [make_trade(0.5, "2026-03-01", regime="TREND_UP"), make_trade(-0.5, "2026-03-02", regime="RANGE"),
          make_trade(0.1, "2026-03-03", regime="TREND_UP")]
    rb = M.regime_breakdown(tr, lambda t: t.regime)
    assert rb["TREND_UP"]["n"] == 2 and rb["TREND_UP"]["expectancy_r"] == pytest.approx(0.3) and rb["RANGE"]["win_rate"] == 0
    assert len(M.fold_expectancies(trades(40, 0.2), 4)) == 4


# ---- pipeline merge and report -----------------------------------------------------------------------------------------
def _phase_a():
    cell = lambda tr: {"trades": tr, "n_setups": len(tr) + 5, "skipped": {"breakeven": 5}}  # noqa: E731
    return {"BTC": {"asset": "BTC", "info": {}, "cells": {("S2-n20", "5m"): cell(trades(250, 0.1)),
                                                          ("S8-20-50", "5m"): cell(trades(30, 0.3))}},
            "ETH": {"asset": "ETH", "info": {}, "cells": {("S2-n20", "5m"): cell(trades(250, 0.1)),
                                                          ("S8-20-50", "5m"): cell(trades(20, 0.3))}}}


def test_assemble_merge_and_classify() -> None:
    cells = PL.assemble_cells(_phase_a())
    assert set(cells) == {("S2-n20", "5m"), ("S8-20-50", "5m")} and cells[("S2-n20", "5m")]["n_setups"] == 510
    PL.merge_stability(cells, {"BTC": {"cells": {("S2-n20", "5m"): {
        "perturb": {"nx0.8": (10.0, 100), "nx1.2": None}, "buckets": {"ATM_040_060@2.5": {"holdout": (1.0, 50), "all": (2.0, 80)}},
        "frictionless": (30.0, 100)}}}})
    c = cells[("S2-n20", "5m")]
    assert c["perturb"]["nx0.8"] == {"BTC": (10.0, 100)} and c["perturb"]["nx1.2"] is None
    PL.merge_control(cells, {"BTC": {"cells": {("S2-n20", "5m"): [(1.0, 10)]}}})
    assert c["control"]["BTC"] == [(1.0, 10)]
    verdicts = PL.classify_all(cells)
    assert verdicts[("S8-20-50", "5m")]["status"] == E.DATA_INSUFFICIENT  # 50 trades only
    assert verdicts[("S2-n20", "5m")]["status"] in (E.EXPERIMENTAL, E.ACCEPTED, E.REJECTED)
    summ = PL.strategy_summary(cells, verdicts)
    assert summ["S2 Donchian Breakout"]["cells"] == 1 and summ["S8 EMA Trend + ADX"]["status"] == E.DATA_INSUFFICIENT
    assert PL.strategy_of("LEGACY-ST-tight-ATM") == PL.LEGACY_GROUP


def test_report_shows_failures_and_never_claims_a_pass_it_did_not_get() -> None:
    cells = PL.assemble_cells(_phase_a())
    verdicts = PL.classify_all(cells)
    res = {"cells": cells, "verdicts": verdicts, "summary": PL.strategy_summary(cells, verdicts),
           "info": {"BTC": {"5m": {"bars": 10, "start": "2026-01-01", "end": "2026-02-01", "funding_z": 1.0, "oi_z": 0.5,
                                   "index_close": 1.0, "iv_percentile": 0.9},
                            "options": {"expiries": 5, "first_expiry": "2026-01-01", "last_expiry": "2026-02-01"}}},
           "errors": {"XAUT": "FileNotFoundError: no iv store"}, "n_cells_per_strategy": {}, "since": "2025-12-01",
           "timeframes": ["5m"], "control_runs": 300}
    md = render(res)
    assert "strategies ACCEPTED" in md and "DATA-INSUFFICIENT" in md and "Run errors" in md and "XAUT" in md
    assert "protocol hash" in md and "Limitations" in md and "model" in md.lower()
    assert "Supertrend" in md and "0.176" in md  # the previous result is preserved verbatim for comparison
    js = to_json(res)
    assert js["cells"]["S2-n20|5m"]["n_trades"] == 500 and js["protocol_hash"]
    assert len(strategies_in_order()) == 10
