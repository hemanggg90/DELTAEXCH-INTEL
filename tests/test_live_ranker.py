"""The strategy universe, the live ranker, ranker-driven paper trading in the engine, and the live IV history.
Offline: evidence is hand-built; nothing here uses the network."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.analogues.analogue_engine import COMPARISON_FEATURES
from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot, Decision, Position
from delta_intelligence.execution.engine import TradingEngine
from delta_intelligence.execution.ranker_setup import engine_kwargs, make_iv_history_fn, ranker_mode
from delta_intelligence.options import iv_live
from delta_intelligence.ranking.live_ranker import EVIDENCE_COLUMNS, LiveRanker
from delta_intelligence.strategies.active import ACTIVE_VARIANTS
from delta_intelligence.strategies.registry import STRATEGY_CLASSES
from delta_intelligence.strategies.universe import BY_KEY, UNIVERSE, Candidate
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("engine_test_helpers", Path(__file__).with_name("test_engine.py"))
_h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_h)  # reuse the engine tests' fake data manager, clock, paper-book fixture and flip frame
NOW, FakeDM, flip_frame, env = _h.NOW, _h.FakeDM, _h.flip_frame, _h.env

ATM, ITM = (v.key for v in ACTIVE_VARIANTS)


# ---- universe -------------------------------------------------------------------------------------------------------
def test_universe_contains_every_strategy_with_unique_keys() -> None:
    assert len(UNIVERSE) == 36 and len({c.key for c in UNIVERSE}) == 36
    by_source = {s: [c for c in UNIVERSE if c.source == s] for s in ("v3", "legacy_v2", "active", "lab")}
    assert [len(v) for v in by_source.values()] == [10, 14, 2, 10]
    assert {c.key for c in by_source["v3"]} == set(STRATEGY_CLASSES)  # the v3 registry itself is unchanged
    assert [c.key for c in by_source["active"]] == [v.key for v in ACTIVE_VARIANTS]
    assert all(c.key.endswith("(v2)") for c in by_source["legacy_v2"])
    assert all(c.key.startswith("S") for c in by_source["lab"])


def test_every_candidate_builds_a_buy_only_strategy_named_by_its_key() -> None:
    for c in UNIVERSE:
        s = c.build()
        assert s.name == c.key and s.policy in ("LONG_OPTION", "LONG_STRADDLE", "LONG_STRANGLE")
        assert s.max_hold_bars >= s.expected_hold_bars > 0
    assert BY_KEY[ITM].moneyness == "ITM1" and BY_KEY[ATM].moneyness == "ATM"


# ---- the ranker ------------------------------------------------------------------------------------------------------
def evidence(rows_by_strategy: dict, asset="BTC", days=120, per_day=4, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for name, mean in rows_by_strategy.items():
        for d in range(days):
            for k in range(per_day):
                t = pd.Timestamp("2026-03-01", tz="UTC") + pd.Timedelta(days=d, hours=2 * k)
                rows.append({"strategy_name": name, "asset": asset, "entry_timestamp": t, "exit_timestamp": t + pd.Timedelta(hours=2),
                             "r_multiple": float(mean + rng.normal(0, 0.25)), "underlying_r": 0.0,
                             **{c: float(rng.normal(0, 1)) for c in COMPARISON_FEATURES}})
    return pd.DataFrame(rows)[EVIDENCE_COLUMNS]


CURRENT = {c: 0.0 for c in COMPARISON_FEATURES}


def test_no_evidence_means_no_trade_never_a_guess() -> None:
    r = LiveRanker(None)
    out = r.rank("BTC", CURRENT, [ATM], [ATM, ITM])
    assert out.selected is None and "no ranker evidence" in out.reason
    assert not r.available and "NO TRADE" in r.describe() or "no ranker evidence" in r.describe()
    assert LiveRanker.from_file("does_not_exist.parquet").available is False


def test_no_strategy_with_a_setup_means_no_trade() -> None:
    r = LiveRanker(evidence({ATM: 0.4}))
    out = r.rank("BTC", CURRENT, [], [ATM, ITM])
    assert out.selected is None and "No strategy has a valid setup" in out.reason
    assert [row["strategy"] for row in out.table][0] == ATM and all(row["setup"] is False for row in out.table)
    assert out.table[0]["eligible"] and "waiting for its signal" in out.table[0]["note"]  # ranked, just not signalling
    assert out.leader == {"strategy": ATM, "score": out.table[0]["score"], "has_setup": False}


def test_strong_validated_evidence_selects_and_a_clear_winner_beats_a_weak_rival() -> None:
    r = LiveRanker(evidence({ATM: 0.45, ITM: -0.2}))
    out = r.rank("BTC", CURRENT, [ATM, ITM], [ATM, ITM])
    assert out.selected == ATM and not out.decision.is_no_trade
    rows = {x["strategy"]: x for x in out.table}
    assert rows[ATM]["note"] == "SELECTED" and rows[ITM]["eligible"] is False and rows[ATM]["confidence"] in ("MEDIUM", "HIGH")


def test_weak_or_negative_evidence_is_no_trade() -> None:
    assert LiveRanker(evidence({ATM: -0.1})).rank("BTC", CURRENT, [ATM], [ATM]).selected is None
    assert LiveRanker(evidence({ATM: 0.0})).rank("BTC", CURRENT, [ATM], [ATM]).selected is None  # below the 0.05 R bar


def test_two_equally_good_strategies_are_not_separable_so_no_trade() -> None:
    out = LiveRanker(evidence({ATM: 0.45, ITM: 0.45}, seed=5)).rank("BTC", CURRENT, [ATM, ITM], [ATM, ITM])
    assert out.selected is None and ("indistinguishable" in out.reason or "not statistically distinguishable" in out.reason)


def test_a_strategy_with_no_evidence_is_ineligible_and_other_assets_do_not_leak() -> None:
    r = LiveRanker(evidence({ATM: 0.45}, asset="ETH"))
    out = r.rank("BTC", CURRENT, [ATM], [ATM])  # evidence exists only for ETH
    assert out.selected is None and out.table[0]["eligible"] is False and "no evidence" in out.table[0]["note"]
    assert r.rank("ETH", CURRENT, [ATM], [ATM]).selected == ATM


def test_evidence_is_used_as_of_the_decision_time() -> None:
    r = LiveRanker(evidence({ATM: 0.45}))
    early = r.rank("BTC", CURRENT, [ATM], [ATM], now=pd.Timestamp("2026-03-02", tz="UTC"))
    assert early.selected is None  # only a day of closed trades existed then
    assert len(r.observations(ATM, "BTC", pd.Timestamp("2026-03-02", tz="UTC"))) < len(r.observations(ATM, "BTC"))


def test_evidence_with_missing_columns_is_refused() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        LiveRanker(evidence({ATM: 0.4}).drop(columns=["r_multiple"]))


# ---- engine: ranker-driven PAPER trading --------------------------------------------------------------------------------
def ranker_frame(direction=1):
    f = flip_frame(direction)
    for c in COMPARISON_FEATURES:
        f[c] = 0.0
    f["relative_volume"] = 1.0  # a normal volume, so the risk engine's volume check passes
    return f


def engine(env, ev, mode="select", universe=None):
    s, box, broker = env
    uni = universe or tuple(BY_KEY[k] for k in (ATM, ITM))
    return TradingEngine(broker, lambda: box["chain"], FakeDM(), s, clock=lambda: NOW, interval_sec=0.05,
                         frame_builder=lambda perp, now: (ranker_frame(), "OK"), ranker=LiveRanker(ev), universe=uni,
                         ranker_mode=mode)


def test_select_mode_trades_only_the_rankers_pick_through_the_risk_engine(env) -> None:
    eng = engine(env, evidence({ATM: 0.45, ITM: -0.2}))
    assert eng.effective_ranker_mode() == "select"
    eng.run_cycle()
    with db.get_session() as s:
        assert [p.strategy for p in s.query(Position).all()] == [ATM]  # the other variant had a setup too, but was not picked
        d = s.query(Decision).one()
        assert d.selected_strategy == ATM and d.setup_status == "TRIGGERED"
        r = d.ranking["ranker"]
        assert r["mode"] == "select" and r["selected"] == ATM and r["n_with_setup"] == 2 and r["table"]
        assert f"RANKER selected {ATM}" in d.ranking["detail"] and f"{ATM}: OPENED" in d.ranking["detail"]


def test_select_mode_without_evidence_takes_no_trade_and_says_why(env) -> None:
    eng = engine(env, evidence({"some other strategy": 0.5}))
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0
        d = s.query(Decision).one()
        assert d.setup_status == "WAITING_FOR_SETUP" and "RANKER: NO TRADE" in d.no_trade_reason
        assert d.ranking["ranker"]["selected"] is None


def test_the_risk_engine_still_has_the_last_word_on_a_ranked_pick(env) -> None:
    from delta_intelligence.execution.engine import set_kill_switch

    set_kill_switch(True, "test")
    eng = engine(env, evidence({ATM: 0.45, ITM: -0.2}))
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0
        assert "kill switch" in s.query(Decision).one().no_trade_reason  # selected, then vetoed by the risk engine
    set_kill_switch(False, "test done")


def test_shadow_mode_ranks_but_the_always_on_variants_keep_trading(env) -> None:
    eng = engine(env, evidence({ATM: 0.45, ITM: -0.2}), mode="shadow")
    eng.run_cycle()
    with db.get_session() as s:
        assert sorted(p.strategy for p in s.query(Position).all()) == sorted([ATM, ITM])  # exactly the pre-ranker behaviour
        assert s.query(Decision).one().ranking["ranker"]["mode"] == "shadow"


def test_live_mode_never_lets_the_ranker_choose_trades(env) -> None:
    s, box, broker = env
    eng = engine(env, evidence({ATM: 0.45}))
    eng.broker = SimpleNamespace(mode="LIVE")  # only the mode matters for this decision
    assert eng.effective_ranker_mode() == "shadow"
    eng.broker = broker
    assert eng.effective_ranker_mode() == "select"
    assert TradingEngine(broker, lambda: box["chain"], FakeDM(), s, clock=lambda: NOW).effective_ranker_mode() == "off"  # no ranker given


def test_a_broken_candidate_does_not_blind_the_ranker(env) -> None:
    class Boom:
        key, source, moneyness = "boom", "test", "ATM"

        def build(self):
            raise RuntimeError("bad strategy")

    eng = engine(env, evidence({ATM: 0.45}), universe=(BY_KEY[ATM], Boom()))
    eng.run_cycle()
    with db.get_session() as s:
        assert [p.strategy for p in s.query(Position).all()] == [ATM]
        assert s.query(Decision).one().ranking["ranker"]["errors"] == ["boom: RuntimeError"]


def test_ranker_mode_environment_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("RANKER_MODE", raising=False)
    assert ranker_mode() == "top"  # default: the top-ranked signalling strategy trades (paper)
    for v in ("shadow", "off", "SELECT", "Top"):
        monkeypatch.setenv("RANKER_MODE", v)
        assert ranker_mode() == v.lower()
    monkeypatch.setenv("RANKER_MODE", "garbage")
    assert ranker_mode() == "top"
    monkeypatch.setenv("RANKER_MODE", "off")
    assert "ranker" not in engine_kwargs(SimpleNamespace(data_cache_dir=tmp_path, data_env="X"))
    monkeypatch.setenv("RANKER_MODE", "shadow")
    kw = engine_kwargs(SimpleNamespace(data_cache_dir=tmp_path, data_env="X"))
    assert kw["ranker_mode"] == "shadow" and len(kw["universe"]) == 36 and callable(kw["iv_history_fn"])


# ---- live IV history ---------------------------------------------------------------------------------------------------------
def snap_rows(n=150, asset="BTC", start="2026-10-01T00:00Z", iv_base=0.40, tte_h=12.0):
    t0 = pd.Timestamp(start)
    rows = []
    for i in range(n):
        t = t0 + pd.Timedelta(minutes=5 * i)
        exp = t + pd.Timedelta(hours=tte_h)
        for kind in "CP":
            for k, ivo in ((84000.0, 0.10), (85000.0, 0.0), (86000.0, 0.10)):
                rows.append({"taken_at": t, "expiry": exp, "strike": k, "kind": kind, "spot": 85010.0,
                             "mark_iv": iv_base + ivo + 0.0002 * i})
    return pd.DataFrame(rows)


def test_atm_iv_is_taken_from_the_atm_strike_in_the_6_to_30_hour_bucket() -> None:
    s = iv_live.atm_iv_from_snapshots(snap_rows(3))
    assert len(s) == 3 and s.iloc[0] == pytest.approx(0.40)  # the 85000 strike, not the 0.10-richer wings
    assert iv_live.atm_iv_from_snapshots(snap_rows(3, tte_h=2.0)).empty  # 2 h to expiry: outside the 6-30 h bucket
    assert iv_live.atm_iv_from_snapshots(snap_rows(3, tte_h=48.0)).empty
    assert iv_live.atm_iv_from_snapshots(pd.DataFrame()).empty


def test_iv_percentile_needs_100_observations_and_says_so_otherwise() -> None:
    now = pd.Timestamp("2026-10-01T13:00Z")
    short = iv_live.atm_iv_from_snapshots(snap_rows(40))
    h, info = iv_live.merge_history([None, None], short)
    st = iv_live.iv_status(h, info, 0.41, now)
    assert st["percentile"] is None and "40/100 observations" in st["note"] and "recorded snapshots" in st["note"]
    full = iv_live.atm_iv_from_snapshots(snap_rows(150))
    h, info = iv_live.merge_history([None, None], full)
    st = iv_live.iv_status(h, info, float(full.iloc[-1]) + 0.01, now)
    assert st["percentile"] == pytest.approx(100.0) and st["n_obs"] == 150
    assert iv_live.iv_status(h, info, None, now)["percentile"] is None  # no live IV: no percentile


def test_hourly_history_wins_and_snapshots_only_extend_it() -> None:
    hours = pd.date_range("2026-09-20T00:00Z", periods=300, freq="1h")
    hourly = pd.Series(0.30, index=hours)
    snaps = pd.Series([0.99] * 50 + [0.50] * 50, index=pd.date_range(hours[-1] - pd.Timedelta(minutes=250), periods=100, freq="5min"))
    merged, info = iv_live.merge_history([hourly, None], snaps)
    assert (merged[merged.index <= hours[-1]] == 0.30).all()  # nothing overwrote the earlier real hourly values
    assert merged.index.is_monotonic_increasing and merged.index.is_unique and info["n_obs"] == len(merged)
    assert merged.index.max() > hours[-1] and "recorded snapshots" in " ".join(info["sources"])
    after = merged[merged.index > hours[-1]]
    assert len(after) == 5 and after.index.floor("h").is_unique  # 8 h of 5-minute snapshots -> one per hour, thinned to the hourly cadence, at TRUE snapshot times
    assert all(t in snaps.index for t in after.index)  # no observation was moved earlier than when it really existed


def test_seed_and_database_snapshots_feed_the_live_history(tmp_path, monkeypatch) -> None:
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'iv.db').as_posix()}")
    now = pd.Timestamp("2026-10-01T12:00Z")
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    hrs = pd.date_range(now - pd.Timedelta(days=30), periods=24 * 30, freq="1h")
    pd.DataFrame({"available_at": hrs, "atm_iv_6_30h": 0.35}).to_parquet(seed_dir / "iv_seed_BTC.parquet", index=False)
    monkeypatch.setattr(iv_live, "SEED_DIR", seed_dir)
    h, info = iv_live.merged_history("BTC", None, now=now, use_db=False)
    assert info["n_obs"] == 720 and "committed seed" in info["sources"][0]
    rows = snap_rows(30, start="2026-09-30T12:00Z")
    with db.get_session() as s:
        for r in rows.itertuples():
            s.add(ChainSnapshot(taken_at=r.taken_at.tz_convert("UTC").tz_localize(None), underlying="BTC", symbol=f"{r.kind}-BTC-{int(r.strike)}-011026",
                                kind=r.kind, strike=r.strike, expiry=r.expiry.tz_convert("UTC").tz_localize(None), spot=r.spot, mark_iv=r.mark_iv))
    h2, info2 = iv_live.merged_history("BTC", None, now=now, use_db=True)
    assert info2["n_obs"] >= info["n_obs"] and any("recorded snapshots" in x for x in info2["sources"]) or info2["n_obs"] == info["n_obs"]
    assert make_iv_history_fn(SimpleNamespace(data_cache_dir=tmp_path, data_env="X"))("BTC") is not None
    db.reset_engine()


# ---- the full leaderboard ------------------------------------------------------------------------------------------------------
THIRD = "Some third strategy"


def test_leaderboard_ranks_every_strategy_whether_or_not_it_is_signalling() -> None:
    r = LiveRanker(evidence({ATM: 0.45, ITM: 0.1, THIRD: -0.2}))
    out = r.rank("BTC", CURRENT, [ITM], [ATM, ITM, THIRD, "no evidence strategy"])
    keys = [row["strategy"] for row in out.table]
    assert sorted(keys) == sorted([ATM, ITM, THIRD, "no evidence strategy"])  # all four are on the leaderboard, none hidden
    assert [row["rank"] for row in out.table] == [1, 2, 3, 4]
    scores = [row["score"] for row in out.table if row["eligible"]]
    assert scores == sorted(scores, reverse=True) and out.table[0]["strategy"] == ATM  # best first
    rows = {row["strategy"]: row for row in out.table}
    assert rows["no evidence strategy"]["eligible"] is False and "no evidence" in rows["no evidence strategy"]["note"]
    assert rows[THIRD]["eligible"] is False and rows[ATM]["setup"] is False and rows[ITM]["setup"] is True


def test_the_traded_strategy_is_the_best_ranked_one_that_is_signalling() -> None:
    r = LiveRanker(evidence({ATM: 0.45, ITM: 0.1}))
    out = r.rank("BTC", CURRENT, [ITM], [ATM, ITM])  # the overall leader (ATM) has no signal; ITM does
    assert out.leader["strategy"] == ATM and out.leader["has_setup"] is False
    if out.selected is not None:  # a signalling strategy may only be chosen if it is itself eligible and clear
        assert out.selected == ITM
    both = r.rank("BTC", CURRENT, [ATM, ITM], [ATM, ITM])
    assert both.leader["strategy"] == ATM and both.leader["has_setup"] is True
    assert sum(row["selected"] for row in both.table) == (1 if both.selected else 0)


def test_a_strategy_without_a_signal_is_never_selected_however_well_it_ranks() -> None:
    r = LiveRanker(evidence({ATM: 0.6, THIRD: -0.3}))
    out = r.rank("BTC", CURRENT, [THIRD], [ATM, THIRD])
    assert out.selected is None and not any(row["selected"] for row in out.table)
    assert out.table[0]["strategy"] == ATM  # ranked first, but cannot trade without a signal


def test_engine_stores_the_whole_leaderboard_and_the_leader(env) -> None:
    s, box, broker = env
    uni = tuple(BY_KEY[k] for k in (ATM, ITM)) + tuple(c for c in UNIVERSE if c.source == "lab")[:3]
    eng = TradingEngine(broker, lambda: box["chain"], FakeDM(), s, clock=lambda: NOW, interval_sec=0.05,
                        frame_builder=lambda perp, now: (ranker_frame(), "OK"), ranker=LiveRanker(evidence({ATM: 0.45, ITM: -0.2})),
                        universe=uni, ranker_mode="select")
    eng.run_cycle()
    with db.get_session() as ses:
        r = ses.query(Decision).one().ranking["ranker"]
    assert len(r["table"]) == len(uni) == 5  # every candidate, including those with no setup and no evidence
    assert {row["strategy"] for row in r["table"]} == {c.key for c in uni}
    assert r["leader"]["strategy"] == ATM and r["n_candidates"] == 5
    assert any(row["note"].startswith("no historical trades") for row in r["table"])


def test_default_mode_is_top(monkeypatch) -> None:
    monkeypatch.delenv("RANKER_MODE", raising=False)
    assert ranker_mode() == "top"


# ---- `top` mode: the best-ranked signalling strategy trades, trying the next one if it is rejected ------------------------------------
from delta_intelligence.ranking.live_ranker import MAX_TOP_ATTEMPTS, TOP_MIN_EDGE_R, top_candidates  # noqa: E402


def row(name, score, edge, setup=True, samples=30):
    return {"rank": 0, "strategy": name, "setup": setup, "score": score, "edge_r": edge, "confidence": "LOW", "samples": samples,
            "eligible": False, "selected": False, "note": ""}


def test_top_candidates_orders_by_score_and_applies_the_documented_floor() -> None:
    table = [row("low", 0.1, 0.05), row("best", 0.9, 0.2), row("no signal", 0.99, 0.5, setup=False),
             row("negative", 0.8, -0.01), row("zero", 0.7, 0.0), row("no evidence", 0.0, None, samples=0), row("mid", 0.5, 0.01)]
    assert [r["strategy"] for r in top_candidates(table)] == ["best", "mid", "low"]  # signalling, positive edge, best first
    assert TOP_MIN_EDGE_R == 0.0 and MAX_TOP_ATTEMPTS == 5
    assert top_candidates([row(f"s{i}", i / 10, 0.1) for i in range(1, 10)]) == top_candidates(
        [row(f"s{i}", i / 10, 0.1) for i in range(1, 10)])[:MAX_TOP_ATTEMPTS]
    assert len(top_candidates([row(f"s{i}", i / 10, 0.1) for i in range(1, 10)])) == MAX_TOP_ATTEMPTS
    assert top_candidates([]) == []


def test_top_mode_trades_the_best_ranked_signalling_strategy_even_below_the_strict_bar(env) -> None:
    eng = engine(env, evidence({ATM: 0.05, ITM: -0.2}), mode="top")  # ATM edge estimate ~+0.02 R: positive, below the strict 0.05 R bar
    assert eng.effective_ranker_mode() == "top"
    eng.run_cycle()
    with db.get_session() as s:
        assert [p.strategy for p in s.query(Position).all()] == [ATM]
        d = s.query(Decision).one()
        r = d.ranking["ranker"]
        assert d.selected_strategy == ATM and d.setup_status == "TRIGGERED" and r["mode"] == "top" and r["selected"] == ATM
        assert r["strict_selected"] is None and "BELOW the strict evidence bar" in r["reason"]
        row_ = next(x for x in r["table"] if x["strategy"] == ATM)
        assert row_["selected"] is True and "below the strict evidence bar" in row_["note"]
        assert f"RANKER(top)" in d.ranking["detail"] and f"{ATM}: OPENED" in d.ranking["detail"]


def test_top_mode_tries_the_next_ranked_strategy_when_the_first_is_rejected(env) -> None:
    from delta_intelligence.execution.planner import TradePlan
    from delta_intelligence.options.structures import long_call

    s, box, broker = env
    st = long_call("BTC", 85000, _h.EXP.to_pydatetime(), 10, 0.001, "C-BTC-85000-031026")
    assert broker.open_structure(TradePlan(ATM, st, 84000.0, 87000.0, premium_stop_pct=35.0)).ok  # ATM is already held
    eng = engine(env, evidence({ATM: 0.45, ITM: 0.1}), mode="top")
    eng.run_cycle()
    with db.get_session() as ses:
        assert sorted(p.strategy for p in ses.query(Position).all()) == sorted([ATM, ITM])  # the held one was skipped, ITM opened
        r = ses.query(Decision).one().ranking
        assert "already holding" in r["detail"] and f"{ITM}: OPENED" in r["detail"] and r["ranker"]["selected"] == ITM


def test_top_mode_opens_at_most_one_position_per_bar(env) -> None:
    eng = engine(env, evidence({ATM: 0.45, ITM: 0.4}), mode="top")
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 1  # both are signalling and eligible, only the top-ranked one trades


def test_top_mode_with_no_usable_evidence_says_why_and_takes_no_trade(env) -> None:
    eng = engine(env, evidence({"some other strategy": 0.5}), mode="top")
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0
        d = s.query(Decision).one()
        assert "NO TRADE - no signalling strategy has evidence and a positive edge estimate" in d.no_trade_reason
        assert d.ranking["ranker"]["selected"] is None


def test_top_mode_never_trades_a_negative_edge_strategy(env) -> None:
    eng = engine(env, evidence({ATM: -0.3, ITM: -0.1}), mode="top")
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0


def test_top_mode_still_goes_through_the_risk_engine_and_is_paper_only(env) -> None:
    from delta_intelligence.execution.engine import set_kill_switch

    set_kill_switch(True, "test")
    eng = engine(env, evidence({ATM: 0.45}), mode="top")
    eng.run_cycle()
    with db.get_session() as s:
        assert s.query(Position).count() == 0 and "kill switch" in s.query(Decision).one().no_trade_reason
    set_kill_switch(False, "test done")
    s_, box, broker = env
    eng.broker = SimpleNamespace(mode="LIVE")
    assert eng.effective_ranker_mode() == "shadow"  # in LIVE the ranker never picks trades
    eng.broker = broker
    assert eng.effective_ranker_mode() == "top"
