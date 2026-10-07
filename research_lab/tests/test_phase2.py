"""Phase 2: option-selection policies, discovery-only selection, signal-vs-implementation classification, the real-data
bar for REAL-DATA-VALIDATED, and one end-to-end path: recorded quote -> database -> selector -> backtester -> metrics ->
report -> dashboard data. Offline."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.backtesting.option_backtest import REAL_ONLY, OptionBacktestConfig, run_option_backtest
from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.backtesting.recorded_chain import RecordedQuotes
from delta_intelligence.database import db
from delta_intelligence.execution import chain_recorder as cr
from delta_intelligence.options import chain_quality as cq
from delta_intelligence.options.chain import OptionChain
from delta_intelligence.options.picking import PickContext
from delta_intelligence.options.pricing import bs_price, greeks, year_fraction
from delta_intelligence.options.selector import SelectorConfig, option_symbol
from delta_intelligence.strategies.base import Setup, Strategy
from delta_intelligence.ui import option_data as od
from helpers import make_trade
from lab import evaluate as E
from phase2 import analysis as A
from phase2.policies import BY_ID, POLICIES, OptionPolicy, PolicyError, make_picker

T0 = pd.Timestamp("2026-09-01T01:00Z")
EXP = [pd.Timestamp(f"2026-09-{d:02d}T12:00Z") for d in (1, 3, 6, 12, 25)]  # 0.46, 2.46, 5.46, 11.46, 24.46 days at T0+5m
BAR = pd.Timedelta(minutes=5)


# ---- policies ------------------------------------------------------------------------------------------------------------
def test_policy_library_is_frozen_sized_and_valid() -> None:
    assert len(POLICIES) == len({p.id for p in POLICIES}) == 14 and POLICIES[0].id == "BASE" and POLICIES[0].default_path
    assert make_picker(BY_ID["BASE"]) is None  # BASE is the project's own path, run without a picker
    for bad in (dict(delta_lo=0.7, delta_hi=0.4), dict(dte_lo=2.0), dict(dte_lo=5.0, dte_hi=3.0), dict(max_spread_pct=0),
                dict(max_iv_percentile=150), dict(rank="best")):
        with pytest.raises(PolicyError):
            OptionPolicy(id="x", **bad)


def ctx(direction="LONG", spot=85000.0, real=False, rv=0.5, ivp=40.0, spread=0.02, liquidity=None, expiries=EXP, hold=2.0):
    iv = 0.5
    t_entry = T0 + BAR

    def fill(kind, k, e, buy):
        t = year_fraction((e - t_entry).total_seconds())
        mid = float(bs_price(spot, k, t, iv, kind))
        return (mid * (1 + spread), mid, iv, "REAL_QUOTE" if real else "MODEL_REAL_IV") if buy else (mid * (1 - spread), mid, iv, "x")

    def abs_delta(e, kind, k):
        return abs(float(greeks(spot, k, year_fraction((e - t_entry).total_seconds()), iv, kind)["delta"]))

    strikes = np.arange(80000, 90001, 500, dtype=float)
    return PickContext(direction=direction, policy="LONG_OPTION" if direction != "VOL" else "LONG_STRADDLE", underlying="BTC",
                       spot=spot, t_entry=t_entry, hold_hours=hold, expiries=list(expiries), strikes_for=lambda e: strikes,
                       abs_delta=abs_delta, fill=fill, greek=lambda kind, k, e: None, liquidity=lambda kind, k, e: liquidity,
                       row={"rv": rv, "iv_percentile": ivp}, contracts=1000, contract_value=0.001,
                       symbol_for=lambda kind, k, e: option_symbol(kind, "BTC", k, e), real_chain=real)


def test_dte_policies_choose_the_nearest_expiry_inside_their_bucket() -> None:
    for pid, expected in (("DTE-2-3", EXP[1]), ("DTE-4-7", EXP[2]), ("DTE-8-14", EXP[3]), ("DTE-15-30", EXP[4])):
        pk = make_picker(BY_ID[pid])(ctx())
        assert pk.expiry == expected, (pid, pk.reason)
        d = (pk.expiry - (T0 + BAR)).total_seconds() / 86400
        assert BY_ID[pid].dte_lo <= d <= BY_ID[pid].dte_hi
    assert make_picker(BY_ID["DTE-15-30"])(ctx(expiries=EXP[:3])).reason == "no_expiry_for_policy"  # nothing that far: no trade


def test_delta_bands_pick_the_contract_closest_to_the_band_middle_deterministically() -> None:
    for pid, lo, hi in (("DELTA-BROAD-030-060", .3, .6), ("DELTA-ITM-050-070", .5, .7), ("DELTA-OTM-020-040", .2, .4)):
        c = ctx()
        a, b = make_picker(BY_ID[pid])(c), make_picker(BY_ID[pid])(c)
        k, e = a.structure.legs[0].strike, a.expiry
        d = c.abs_delta(e, "C", k)
        assert lo <= d <= hi and a.detail["strike"] == b.detail["strike"] == k  # same input, same answer
        assert a.structure.name == "LONG_CALL" and make_picker(BY_ID[pid])(ctx("SHORT")).structure.name == "LONG_PUT"
    itm = make_picker(BY_ID["DELTA-ITM-050-070"])(ctx()).structure.legs[0].strike
    otm = make_picker(BY_ID["DELTA-OTM-020-040"])(ctx()).structure.legs[0].strike
    assert itm < 85000 < otm  # an ITM call has a lower strike, an OTM call a higher one


def test_filters_refuse_with_a_named_reason_and_never_force_a_trade() -> None:
    assert make_picker(BY_ID["FILTER-SPREAD-LE-6"])(ctx(spread=0.02)).structure is not None  # 4% total spread passes
    assert make_picker(BY_ID["FILTER-SPREAD-LE-6"])(ctx(spread=0.08)).reason == "filter_spread"
    assert make_picker(BY_ID["FILTER-IVRV-LE-1.1"])(ctx(rv=0.2)).reason == "filter_iv_rv"  # IV 0.5 vs RV 0.2
    assert make_picker(BY_ID["FILTER-IVRV-LE-1.1"])(ctx(rv=None)).reason == "filter_iv_rv"  # unknown RV: refuse, never assume
    assert make_picker(BY_ID["FILTER-IVPCT-LE-50"])(ctx(ivp=80.0)).reason == "filter_iv_percentile"
    assert make_picker(BY_ID["FILTER-IVPCT-LE-50"])(ctx(ivp=None)).reason == "filter_iv_percentile"
    assert make_picker(BY_ID["FILTER-PREMIUM-LE-0.5"])(ctx()).reason == "filter_premium"  # a ~1% premium exceeds 0.5% of spot
    assert make_picker(BY_ID["FILTER-THETA-LE-40"])(ctx()).reason == "filter_theta"  # a 0.46-day ATM option decays far faster


def test_liquidity_policy_cannot_run_on_modelled_data_and_uses_real_oi_when_present() -> None:
    p = make_picker(BY_ID["LIQ-OI100-VOL10"])
    assert p(ctx(liquidity=None)).reason == "needs_real_liquidity_data"  # modelled data has no OI or volume: not faked
    assert p(ctx(real=True, liquidity={"open_interest": 5.0, "volume": 50.0})).reason == "filter_open_interest"
    assert p(ctx(real=True, liquidity={"open_interest": 500.0, "volume": 1.0})).reason == "filter_volume"
    assert p(ctx(real=True, liquidity={"open_interest": 500.0, "volume": 50.0})).structure is not None


def test_volatility_view_gets_an_atm_straddle_and_delta_bands_do_not_apply() -> None:
    pk = make_picker(BY_ID["DTE-4-7"])(ctx("VOL"))
    assert pk.structure.name == "LONG_STRADDLE" and len({leg.strike for leg in pk.structure.legs}) == 1 and pk.expiry == EXP[2]
    from phase2.runner2 import applicable

    class Vol:
        is_vol = True

    assert not applicable("DELTA-ITM-050-070", Vol()) and applicable("DTE-4-7", Vol()) and not applicable("LIQ-OI100-VOL10", Vol())


# ---- analysis ----------------------------------------------------------------------------------------------------------
def trades(n, net, under=None, gross=None, start="2026-06-01", step_h=4):
    base = pd.Timestamp(start, tz="UTC")
    out = []
    for i in range(n):
        t = make_trade(net, str(base + pd.Timedelta(hours=step_h * i)), entry_idx=i * 20)
        t.underlying_r = net if under is None else under
        g = (net * t.max_loss + t.fees) if gross is None else gross * t.max_loss
        t.gross_pnl, t.spread_cost = g, g - net * t.max_loss - t.fees
        t.entry_features = {"premium_pct_spot": 0.5, "spread_pct": 4.0, "iv": 0.5, "rv": 0.45, "iv_rv": 1.1, "abs_delta": 0.5,
                            "dte_days": 1.0, "theta_pct_premium": 30.0, "gamma": 1e-4, "vega": 40.0}
        out.append(t)
    return out


def test_metrics_and_attribution_add_up() -> None:
    t = trades(60, 0.1, gross=0.3)
    m, a = A.option_metrics(t), A.attribution(t)
    assert m["n"] == 60 and m["net_r"] == pytest.approx(0.1) and m["gross_r"] == pytest.approx(0.3) and m["slippage_usd"] is None
    assert a["gross_r"] + a["spread_r"] + a["fees_r"] == pytest.approx(a["net_r"]) and abs(a["identity_gap_r"]) < 1e-9
    assert m["pct_real_entry"] == 0.0 and A.option_metrics([]) == {"n": 0}


def test_signal_vs_option_classification() -> None:
    e = lambda n, net, **k: trades(n, net, start="2026-04-01", **k) + trades(n, net, start="2026-07-02", **k)  # noqa: E731
    keep = A.signal_vs_option(e(60, 0.1, under=0.4, gross=0.3))
    assert keep["classification"].startswith("SIGNAL EDGE PRESERVED")
    cost = A.signal_vs_option(e(60, -0.05, under=0.4, gross=0.1))
    assert cost["classification"].startswith(A.OPTION_IMPLEMENTATION_FAILURE) and "costs" in cost["classification"]
    decay = A.signal_vs_option(e(60, -0.2, under=0.4, gross=-0.1))
    assert decay["classification"].startswith(A.OPTION_IMPLEMENTATION_FAILURE) and "decay" in decay["classification"]
    none = A.signal_vs_option(e(60, -0.1, under=-0.2, gross=-0.05))
    assert none["classification"].startswith("NO SIGNAL EDGE")
    luck = A.signal_vs_option(e(60, 0.1, under=-0.1, gross=0.3))
    assert "WITHOUT A SHOWN SIGNAL EDGE" in luck["classification"]
    assert A.signal_vs_option(trades(10, 0.1))["classification"] == "DATA-INSUFFICIENT"
    assert A.signal_vs_option([])["classification"] == "DATA-INSUFFICIENT"


def test_policy_selection_uses_only_discovery_trades() -> None:
    order = ["BASE", "P1", "P2"]
    disc = lambda r: trades(120, r, start="2026-03-01", step_h=6)  # noqa: E731
    hold_a = lambda r: trades(60, r, start="2026-07-02")  # noqa: E731
    one = {"BASE": disc(0.0) + hold_a(0.5), "P1": disc(0.1) + hold_a(-0.9), "P2": disc(-0.2) + hold_a(0.9)}
    two = {"BASE": disc(0.0) + hold_a(-0.9), "P1": disc(0.1) + hold_a(0.9), "P2": disc(-0.2) + hold_a(-0.9)}  # holdouts flipped
    s1, s2 = A.select_policy(one, order), A.select_policy(two, order)
    assert s1["selected"] == s2["selected"] == "P1"  # the holdout cannot change the choice
    thin = {"BASE": trades(50, 0.5, start="2026-03-01"), "P1": trades(50, 0.9, start="2026-03-01"), "P2": []}
    assert A.select_policy(thin, order)["selected"] == "BASE"  # no policy has 100 discovery trades: BASE kept
    tie = {"BASE": disc(0.1), "P1": disc(0.1), "P2": disc(0.1)}
    assert A.select_policy(tie, order)["selected"] == "BASE"  # ties go to the earlier policy


def test_policy_breadth_counts_only_evaluable_policies() -> None:
    per = {"A": trades(40, 0.1, start="2026-07-02"), "B": trades(40, -0.1, start="2026-07-02"), "C": trades(5, 0.9, start="2026-07-02")}
    share, n = A.policy_breadth(per, ["A", "B", "C", "D"])
    assert (share, n) == (0.5, 2)  # C has too few holdout trades, D none: neither counts


def cell_for(net, assets=("BTC", "ETH"), with_stability=True):
    jit = [0.3, -0.2, 0.1, -0.1]
    c = {"assets": {a: {"trades": [make_trade(net + jit[i % 4], str(pd.Timestamp("2026-06-01", tz="UTC") + pd.Timedelta(hours=4 * i)))
                                   for i in range(250)], "n_setups": 250, "skipped": {}} for a in assets}, "n_setups": 500}
    if with_stability:
        c.update(perturb={"x": {"BTC": (30.0, 200)}}, control={"BTC": [(0.0, 100)] * 300},
                 buckets={f"{p}@2.5": {"holdout": {"BTC": (5.0, 100)}, "all": {}} for p in ("BASE", "DELTA-BROAD-030-060", "DELTA-ITM-050-070")})
    return c


def test_policy_breadth_gate_downgrades_an_otherwise_accepted_cell() -> None:
    ok = A.final_status(cell_for(0.15), 2, breadth=0.8)
    assert ok["status"] == E.ACCEPTED and ok["gates"]["policy_breadth"]["ok"] is True
    narrow = A.final_status(cell_for(0.15), 2, breadth=0.2)
    assert narrow["status"] == E.EXPERIMENTAL and any("policy_breadth" in r for r in narrow["reasons"])
    assert A.final_status(cell_for(-0.1), 2, breadth=0.9)["status"] == E.REJECTED  # the existing gates are not relaxed


def real_cell(n, source="REAL_QUOTE"):
    cell = cell_for(0.15)
    cell["assets"] = {a: {"trades": [make_trade(0.15, str(pd.Timestamp("2026-06-01", tz="UTC") + pd.Timedelta(hours=4 * i))) for i in range(n)],
                          "n_setups": n, "skipped": {}} for a in ("BTC", "ETH")}
    for r in cell["assets"].values():
        for t in r["trades"]:
            t.entry_price_source = t.exit_price_source = source
    return cell


def test_real_data_validated_requires_the_whole_real_data_bar() -> None:
    sel = real_cell(250)
    base = A.final_status(sel, 1, breadth=0.9)
    assert base["status"] == E.ACCEPTED and base["real_data"]["checked"] is False  # no real results at all: stays ACCEPTED
    few = A.final_status(sel, 1, 0.9, real_cell(5), snapshot_days=40)
    assert few["status"] == E.ACCEPTED and "real trades (need 200)" in few["real_data"]["detail"]
    short = A.final_status(sel, 1, 0.9, real_cell(250), snapshot_days=3)
    assert short["status"] == E.ACCEPTED and "days of recorded snapshots" in short["real_data"]["detail"]
    modelled = real_cell(250)
    mixed = A.final_status(real_cell(250, source="MODELED"), 1, 0.9, modelled, snapshot_days=40)
    assert mixed["status"] == E.ACCEPTED and "priced from real quotes" in mixed["real_data"]["detail"]
    full = A.final_status(sel, 1, 0.9, real_cell(250), snapshot_days=40)
    assert full["status"] == A.REAL_DATA_VALIDATED and full["real_data"]["validated"] is True
    not_accepted = A.final_status(cell_for(-0.1), 1, 0.9, real_cell(250), snapshot_days=40)
    assert not_accepted["status"] == E.REJECTED  # REAL-DATA-VALIDATED can never rescue a cell that fails the gates


def test_real_thresholds_are_configurable_and_documented() -> None:
    assert A.DEFAULT_REAL.min_real_fraction == 0.8 and "Rationale" in A.RealDataThresholds.__doc__
    lax = A.RealDataThresholds(min_snapshot_days=1)
    assert A.final_status(real_cell(250), 1, 0.9, real_cell(250), real=lax, snapshot_days=2)["status"] == A.REAL_DATA_VALIDATED


# ---- end to end: recorded quote -> database -> selector -> backtester -> metrics -> report -> dashboard -------------------
class Scripted(Strategy):
    name = "scripted"
    expected_hold_bars, max_hold_bars = 24, 48

    def raw_signals(self, f):
        return self.out(pd.Series(0, index=f.index), f["close"], f["close"])


def tick(kind, k, spot, at, expiry, iv=0.5):
    t = year_fraction((expiry - at).total_seconds())
    mid = float(bs_price(spot, k, t, iv, kind))
    g = greeks(spot, k, t, iv, kind)
    return {"symbol": option_symbol(kind, "BTC", k, expiry), "strike_price": str(k), "mark_price": str(mid), "oi": "5", "oi_contracts": "5000",
            "volume": 100.0, "timestamp": int(at.timestamp() * 1e6), "product_id": 1, "tick_size": "0.1", "contract_value": "0.001",
            "quotes": {"best_bid": str(max(mid - 5, 0.1)), "best_ask": str(mid + 5), "bid_size": "300", "ask_size": "300", "mark_iv": str(iv)},
            "greeks": {"delta": str(float(g["delta"])), "gamma": str(float(g["gamma"])), "theta": str(float(g["theta_day"])),
                       "vega": str(float(g["vega"])), "rho": "0", "spot": str(spot)}}


def test_recorded_quote_flows_through_every_layer(tmp_path) -> None:
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}")
    n = 60
    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 87500, n - 5)])
    ts = pd.date_range(T0, periods=n, freq="5min")
    f = pd.DataFrame({"timestamp": ts, "open": path, "high": path + 20, "low": path - 20, "close": path, "volume": 1.0,
                      "index_close": path, "index_realized_vol_1d": 0.5})
    expiry = EXP[0]
    for t, spot in zip(ts, path):  # 1. record (the project's recorder, from Delta-shaped tickers)
        at = t + BAR
        chain = OptionChain.from_tickers([tick(kd, k, float(spot), at, expiry) for kd in "CP" for k in np.arange(84400, 85801, 200.0)])
        assert cr.record(chain, {"BTC": float(spot)}, at.to_pydatetime()) > 0
    raw = cq.load_snapshots()  # 2. database -> quality -> as-of provider
    assessed, rep = cq.assess(raw)
    assert rep.tier_counts[cq.REAL_QUOTE] == len(raw) and rep.n_snapshots == n
    rec = RecordedQuotes(assessed, "BTC")
    h = pd.date_range("2026-08-31T00:00Z", periods=120, freq="1h")
    iv = pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_0_6h": 0.5, "atm_iv_6_30h": 0.5, "atm_iv_30_54h": 0.5})
    market = OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], iv,
                               listed_strikes={e: np.arange(80000, 90001, 200.0) for e in EXP})
    setups = [Setup(T0, "LONG", 85000.0, 84500.0, 87000.0)]
    cfg = OptionBacktestConfig(pricing_mode=REAL_ONLY, apply_breakeven_gate=False, selector=SelectorConfig(),
                               contract_picker=make_picker(BY_ID["DELTA-BROAD-030-060"]), selection_name="DELTA-BROAD-030-060")
    res = run_option_backtest(Scripted(), f, market, cfg, setups=setups, recorded=rec)  # 3. selector -> backtester (real)
    assert res.trades, res.skipped
    t = res.trades[0]
    assert t.entry_price_source == t.exit_price_source == "REAL_QUOTE" and t.selection == "DELTA-BROAD-030-060"
    m = A.option_metrics(res.trades)  # 4. metrics
    assert m["pct_real_both"] == 100.0 and m["avg_spread_pct"] > 0 and m["net_r"] == pytest.approx(t.net_r)
    assert A.attribution(res.trades)["identity_gap_r"] == pytest.approx(0, abs=1e-9)
    # 5. report + 6. dashboard data (the same shapes the pipeline writes)
    from phase2.report2 import md_table

    assert "net_r" in "\n".join(md_table(pd.DataFrame([m])))
    summary = {"policies": ["BASE", "DELTA-BROAD-030-060"], "cells": {"S|5m": {"policies": {
        "DELTA-BROAD-030-060": {"holdout": {"n": 40, "net_r": 0.1}, "discovery": {"n": 40, "net_r": -0.1}},
        "BASE": {"holdout": {"n": 10, "net_r": 0.5}, "discovery": {"n": 40, "net_r": 0.0}}}}}}
    hm = od.heatmap_frame(summary, "holdout")
    assert hm.loc["S 5m", "DELTA-BROAD-030-060"] == 0.1 and np.isnan(hm.loc["S 5m", "BASE"])  # <30 trades -> blank, not shown
    health = od.recorder_health(raw, now=pd.Timestamp(raw["taken_at"].max()) + pd.Timedelta(minutes=2))
    assert health["alive"] and health["rows"] == len(raw)
    stalled = od.recorder_health(raw, now=pd.Timestamp(raw["taken_at"].max()) + pd.Timedelta(hours=3))
    assert not stalled["alive"] and "STALLED" in stalled["verdict"]
    assert not od.recorder_health(raw.iloc[0:0])["alive"]
    db.reset_engine()


def test_phase2_protocol_matches_the_frozen_hash() -> None:
    """Any change to a policy, threshold or Phase 2 source file fails this until it is deliberately re-frozen and disclosed."""
    from pathlib import Path

    from phase2.protocol2 import protocol2_hash

    frozen = Path(__file__).resolve().parents[1] / "phase2" / "FROZEN_PHASE2_HASH.txt"
    assert frozen.exists(), "Phase 2 has not been frozen yet"
    assert protocol2_hash() == frozen.read_text().strip()


def test_phase2_never_writes_orders_or_touches_the_broker() -> None:
    from pathlib import Path

    forbidden = ["place" + "_order(", "allow_orders" + "=True", "with" + "draw", "close" + "_all", "delta_intelligence.brokers"]
    for p in (Path(__file__).resolve().parents[1] / "phase2").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert not [f for f in forbidden if f in text], p.name


# ---- memory-safe summaries must equal the trade-list computation ---------------------------------------------------------------
def varied(n, net, start, seed):
    rng = np.random.default_rng(seed)
    out = trades(n, net, start=start)
    for t in out:
        t.net_r = float(net + rng.normal(0, 0.3))
        t.entry_features = {**t.entry_features, "dte_days": float(rng.uniform(0.2, 25)), "abs_delta": float(rng.uniform(0.2, 0.7)),
                            "spread_pct": float(rng.uniform(1, 25)), "iv_rv": float(rng.uniform(0.6, 1.8))}
    return out


def test_bucket_sums_merge_exactly_like_pooled_trades() -> None:
    a, b = varied(120, 0.05, "2026-06-01", 1), varied(90, -0.02, "2026-07-02", 2)
    direct = A.bucket_frames(A.bucket_sums(a + b))
    merged = A.bucket_frames(A.merge_bucket_sums([A.bucket_sums(a), A.bucket_sums(b)]))
    for feat in A.BUCKETS:
        pd.testing.assert_frame_equal(direct[feat].reset_index(drop=True), merged[feat].reset_index(drop=True))
    old = A.by_bucket(a + b, "dte_days", *A.BUCKETS["dte_days"])  # the trade-list version agrees on counts and means
    new = direct["dte_days"].set_index("bucket")
    for _, r in old.iterrows():
        assert new.loc[r["bucket"], "n"] == r["n"] and new.loc[r["bucket"], "net_r"] == pytest.approx(r["net_r"])


def test_pooled_metrics_equal_metrics_of_the_pooled_trades_where_poolable() -> None:
    a, b = varied(120, 0.05, "2026-06-01", 3), varied(60, -0.05, "2026-07-02", 4)
    pooled = A.pool_metrics([A.option_metrics(a), A.option_metrics(b)])
    direct = A.option_metrics(a + b)
    assert pooled["n"] == direct["n"] and pooled["net_r"] == pytest.approx(direct["net_r"]) and pooled["win_rate"] == pytest.approx(direct["win_rate"])
    assert pooled["gross_r"] == pytest.approx(direct["gross_r"]) and pooled["fees_usd"] == pytest.approx(direct["fees_usd"])
    assert pooled["profit_factor"] is None and pooled["max_drawdown_r"] is None  # not poolable: never fabricated
    assert A.pool_metrics([{"n": 0}, {"n": 0}]) == {"n": 0}


def test_selection_and_breadth_from_sums_equal_the_trade_list_versions() -> None:
    order = ["BASE", "P1", "P2"]
    per = {pid: varied(150, r, "2026-03-01", i) + varied(60, -r, "2026-07-02", 10 + i) for i, (pid, r) in enumerate(zip(order, (0.0, 0.1, -0.1)))}
    sums = {pid: (len(A.split(t)[0]), A._m(x.net_r for x in A.split(t)[0])) for pid, t in per.items()}
    assert A.select_from_sums(sums, order)["selected"] == A.select_policy(per, order)["selected"] == "P1"
    hold = {pid: (len(A.split(t)[1]), A._m(x.net_r for x in A.split(t)[1])) for pid, t in per.items()}
    assert A.breadth_from_sums(hold, order) == A.policy_breadth(per, order)


def test_matrix_worker_spills_trades_and_returns_only_summaries(tmp_path, monkeypatch) -> None:
    import pickle

    from phase2 import runner2

    class FakeData:
        asset, info, frames = "BTC", {}, {"5m": None}

    class Strat:
        is_vol = False

        def historical_setups(self, f):
            return []

    class Res:
        n_setups, skipped, unpriced_exits = 3, {"breakeven": 2}, []
        trades = varied(40, 0.1, "2026-07-02", 5)

    monkeypatch.setattr(runner2.runner, "load_asset", lambda *a: FakeData())
    monkeypatch.setattr(runner2.runner, "build_for", lambda vid, tf: (Strat(), None, tf))
    monkeypatch.setattr(runner2, "map_setups", lambda s, tf: s)
    monkeypatch.setattr(runner2, "backtest2", lambda *a, **k: Res())
    out = runner2.matrix("BTCUSD", "2026-01-01", ("5m",), [("S2-n20", "5m")], ["BASE", "DTE-4-7"], str(tmp_path))
    c = out["cells"][("S2-n20", "5m", "BASE")]
    assert "trades" not in c and c["hold"][0] == 40 and c["periods"]["holdout"]["n"] == 40 and c["skipped"] == {"breakeven": 2}
    spilled = runner2.load_spilled(str(tmp_path), "BTC", "S2-n20", "5m", "DTE-4-7")
    assert len(spilled) == 40 and spilled[0].net_r == Res.trades[0].net_r
    assert runner2.load_spilled(str(tmp_path), "BTC", "nope", "5m", "BASE") == []
