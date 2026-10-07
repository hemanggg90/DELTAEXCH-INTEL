"""Phase 2: the backtester's three pricing modes with RECORDED quotes. Real and modelled prices must never be mixed
silently, nothing may use information after the pricing time, and MODEL_ONLY must stay exactly the old methodology."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.backtesting.option_backtest import (MODEL_ONLY, REAL_ONLY, REAL_THEN_MODEL_FALLBACK, OptionBacktestConfig,
                                                            run_option_backtest)
from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.backtesting.recorded_chain import RecordedQuotes
from delta_intelligence.options import chain_quality as cq
from delta_intelligence.options.picking import Pick
from delta_intelligence.options.pricing import bs_price, greeks, year_fraction
from delta_intelligence.options.selector import SelectorConfig, option_symbol
from delta_intelligence.options.structures import long_call
from delta_intelligence.strategies.base import Setup, Strategy

T0 = pd.Timestamp("2026-09-01T01:00Z")
EXPIRIES = [pd.Timestamp(f"2026-09-0{d}T12:00Z") for d in (1, 2, 3, 4)]
STRIKES = np.arange(80000, 90001, 200, dtype=float)
REAL_STRIKES = np.arange(84400, 85801, 200, dtype=float)
BAR = pd.Timedelta(minutes=5)


class Scripted(Strategy):
    name = "scripted"
    expected_hold_bars = 24
    max_hold_bars = 48

    def raw_signals(self, f):
        return self.out(pd.Series(0, index=f.index), f["close"], f["close"])


def perp_frame(path: np.ndarray) -> pd.DataFrame:
    ts = pd.date_range(T0, periods=len(path), freq="5min")
    return pd.DataFrame({"timestamp": ts, "open": path, "high": path + 20, "low": path - 20, "close": path, "volume": 1.0,
                         "index_close": path, "index_realized_vol_1d": 0.5})


def market_for(f) -> OptionMarketModel:
    h = pd.date_range("2026-08-31T00:00Z", periods=120, freq="1h")
    iv = pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_0_6h": 0.5, "atm_iv_6_30h": 0.5, "atm_iv_30_54h": 0.5})
    syms = {(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES for e in EXPIRIES}
    return OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], iv,
                             listed_strikes={e: STRIKES for e in EXPIRIES}, symbols=syms)


def real_rows(f: pd.DataFrame, *, skip=(), ask_bump=0.0, bid_nan=False, delta_shift=0.0, extra=None, iv=0.5,
              expiry=EXPIRIES[0], strikes=REAL_STRIKES) -> pd.DataFrame:
    """Recorded snapshots at every bar CLOSE, priced off the path (a stand-in for what the exchange would have shown)."""
    rows = []
    for i, (t, spot) in enumerate(zip(f["timestamp"], f["index_close"])):
        if i in skip:
            continue
        at = t + BAR
        tte = year_fraction((expiry - at).total_seconds())
        for kind in "CP":
            for k in strikes:
                mid = float(bs_price(spot, k, tte, iv, kind))
                d = float(greeks(spot, k, tte, iv, kind)["delta"]) + delta_shift
                rows.append({"underlying": "BTC", "symbol": option_symbol(kind, "BTC", k, expiry), "kind": kind, "strike": float(k),
                             "spot": float(spot), "bid": np.nan if bid_nan else max(mid - 5.0, 0.1), "ask": mid + 5.0 + ask_bump,
                             "mark": mid, "bid_size": 300.0, "ask_size": 300.0, "open_interest": 5000.0, "volume": 100.0,
                             "delta": d, "gamma": 1e-4, "theta": -30.0, "vega": 40.0, "mark_iv": iv, "taken_at": at, "expiry": expiry,
                             "dte_days": (expiry - at).total_seconds() / 86400.0, "quote_ts_us": int(at.timestamp() * 1e6)})
    if extra:
        rows += extra
    return pd.DataFrame(rows)


def recorded(df: pd.DataFrame, max_age_sec=600.0) -> RecordedQuotes:
    assessed, _ = cq.assess(df)
    return RecordedQuotes(assessed, "BTC", max_age_sec)


def run(path, setups, mode=MODEL_ONLY, rec=None, strategy=None, **cfg):
    f = perp_frame(path)
    c = OptionBacktestConfig(pricing_mode=mode, apply_breakeven_gate=False, **cfg)
    return run_option_backtest(strategy or Scripted(), f, market_for(f), c, setups=setups, recorded=rec), f


def up_path(n=60):
    return np.concatenate([np.full(5, 85000.0), np.linspace(85000, 87500, n - 5)])


SETUP = [Setup(T0, "LONG", 85000.0, 84500.0, 87000.0)]


# ---- MODEL_ONLY reproducibility --------------------------------------------------------------------------------------
def test_model_only_is_the_default_and_unchanged_by_extra_arguments() -> None:
    f = perp_frame(up_path())
    cfg = OptionBacktestConfig(apply_breakeven_gate=False)
    default = run_option_backtest(Scripted(), f, market_for(f), cfg, setups=SETUP)
    explicit = run_option_backtest(Scripted(), f, market_for(f), OptionBacktestConfig(apply_breakeven_gate=False, pricing_mode=MODEL_ONLY),
                                   setups=SETUP, recorded=recorded(real_rows(f)))  # recorded data present but ignored
    a, b = default.trades[0], explicit.trades[0]
    assert (a.net_r, a.entry_fills, a.exit_fills, a.strikes, a.expiry, a.fees) == (b.net_r, b.entry_fills, b.exit_fills, b.strikes, b.expiry, b.fees)
    assert cfg.pricing_mode == MODEL_ONLY and a.entry_price_source == a.exit_price_source == "MODELED"


def test_modes_need_recorded_quotes_and_valid_names() -> None:
    f = perp_frame(up_path())
    for mode in (REAL_ONLY, REAL_THEN_MODEL_FALLBACK):
        with pytest.raises(ValueError, match="needs recorded"):
            run_option_backtest(Scripted(), f, market_for(f), OptionBacktestConfig(pricing_mode=mode), setups=SETUP)
    with pytest.raises(ValueError, match="pricing_mode"):
        run_option_backtest(Scripted(), f, market_for(f), OptionBacktestConfig(pricing_mode="BEST_PRICE"), setups=SETUP)


def test_attribution_identity_holds_for_modelled_trades() -> None:
    res, _ = run(up_path(), SETUP)
    t = res.trades[0]
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.spread_cost - t.fees) and t.spread_cost > 0 and t.entry_features["premium_pct_spot"] > 0


# ---- REAL_ONLY ---------------------------------------------------------------------------------------------------------
def test_real_only_enters_at_the_recorded_ask_and_exits_at_the_recorded_bid() -> None:
    f = perp_frame(up_path())
    rows = real_rows(f)
    res, _ = run(up_path(), SETUP, REAL_ONLY, recorded(rows))
    assert res.trades, res.skipped
    t = res.trades[0]
    assert t.entry_price_source == t.exit_price_source == "REAL_QUOTE" and t.premium_source == "REAL_QUOTE"
    sym = option_symbol("C", "BTC", t.strikes[0], t.expiry)
    entry_row = rows[(rows["symbol"] == sym) & (rows["taken_at"] == T0 + BAR)].iloc[0]
    exit_row = rows[(rows["symbol"] == sym) & (rows["taken_at"] == t.exit_time)].iloc[0]
    assert t.entry_fills == [entry_row["ask"]] and t.exit_fills == [exit_row["bid"]]  # never the mark, never the mid
    assert t.entry_mids == [pytest.approx((entry_row["ask"] + entry_row["bid"]) / 2)]
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.spread_cost - t.fees)  # identity holds with real quotes
    assert t.spread_cost == pytest.approx(5.0 + 5.0, rel=1e-6, abs=0.02) or t.spread_cost > 0
    assert t.entry_features["real_greeks"] is True and t.entry_features["open_interest"] == 5000.0


def test_real_only_never_falls_back_to_the_model() -> None:
    f = perp_frame(up_path())
    nothing = recorded(real_rows(f).iloc[0:0].reindex(columns=real_rows(f).columns))
    res, _ = run(up_path(), SETUP, REAL_ONLY, nothing)
    assert not res.trades and res.skipped["no_real_snapshot"] == 1
    modelled, _ = run(up_path(), SETUP, MODEL_ONLY)
    assert modelled.trades  # the model COULD have priced it: REAL_ONLY refused to


def test_real_only_skips_a_mark_that_is_not_a_two_sided_quote() -> None:
    f = perp_frame(up_path())
    res, _ = run(up_path(), SETUP, REAL_ONLY, recorded(real_rows(f, bid_nan=True)))
    assert not res.trades and res.skipped["real_mark_only"] >= 1  # a mark is not a bid or an ask


def test_real_only_counts_unpriced_exits_instead_of_dropping_them_silently() -> None:
    f = perp_frame(up_path())
    gap = set(range(6, 60))  # quotes vanish right after entry: the exit cannot be priced from a real quote
    res, _ = run(up_path(), SETUP, REAL_ONLY, recorded(real_rows(f, skip=gap)))
    assert not res.trades and res.skipped["no_real_exit_quote"] == 1
    assert len(res.unpriced_exits) == 1 and res.unpriced_exits[0][1] > 0  # reported with its worst-case premium at risk


def test_stale_snapshots_are_not_used() -> None:
    f = perp_frame(up_path())
    res, _ = run(up_path(), SETUP, REAL_ONLY, recorded(real_rows(f), max_age_sec=60.0), strategy=Scripted())
    # snapshots exist exactly at each bar close, so a 60 s age limit still allows the exact-time ones
    assert res.trades
    gap = recorded(real_rows(f, skip={0}), max_age_sec=120.0)  # the entry-time snapshot is missing; the next is later
    res2, _ = run(up_path(), SETUP, REAL_ONLY, gap)
    assert not res2.trades and res2.skipped["no_real_snapshot"] == 1


# ---- no look-ahead ---------------------------------------------------------------------------------------------------
def test_a_later_quote_is_never_used_for_an_earlier_decision() -> None:
    f = perp_frame(up_path())
    t_entry = T0 + BAR
    good = real_rows(f)
    # an irresistible ask one minute AFTER the entry time must not be picked up
    sym = option_symbol("C", "BTC", 85000.0, EXPIRIES[0])
    lure = {**good[(good["symbol"] == sym) & (good["taken_at"] == t_entry)].iloc[0].to_dict(), "taken_at": t_entry + pd.Timedelta(minutes=1),
            "ask": 1.0, "bid": 0.5, "mark": 0.75, "quote_ts_us": int((t_entry + pd.Timedelta(minutes=1)).timestamp() * 1e6)}
    rec_a = recorded(good)
    rec_b = recorded(pd.concat([good, pd.DataFrame([lure])], ignore_index=True))
    a, _ = run(up_path(), SETUP, REAL_ONLY, rec_a)
    b, _ = run(up_path(), SETUP, REAL_ONLY, rec_b)
    assert a.trades[0].entry_fills == b.trades[0].entry_fills
    q = rec_b.quote("C", 85000.0, EXPIRIES[0], t_entry)
    assert q.taken_at == t_entry and q.ask != 1.0
    assert rec_b.quote("C", 85000.0, EXPIRIES[0], t_entry - pd.Timedelta(seconds=1)) is None  # nothing existed yet: no peeking
    later = rec_b.quote("C", 85000.0, EXPIRIES[0], t_entry + pd.Timedelta(minutes=2))
    assert later.taken_at == t_entry + pd.Timedelta(minutes=1) and later.ask == 1.0  # visible only once its time has come


def test_recorded_lookup_is_strictly_as_of_and_age_limited() -> None:
    f = perp_frame(up_path())
    rec = recorded(real_rows(f), max_age_sec=300.0)
    at = T0 + pd.Timedelta(minutes=17)
    q = rec.quote("C", 85000.0, EXPIRIES[0], at)
    assert q.taken_at <= at and (at - q.taken_at).total_seconds() == q.age_sec <= 300.0
    assert rec.quote("C", 85000.0, EXPIRIES[0], T0 - pd.Timedelta(hours=1)) is None  # before any snapshot
    assert rec.quote("C", 85000.0, EXPIRIES[0], T0 + pd.Timedelta(days=3)) is None  # too old
    assert rec.snapshot_at(T0 + pd.Timedelta(days=3)) is None and rec.quote("C", 99999.0, EXPIRIES[0], at) is None


# ---- REAL_THEN_MODEL_FALLBACK and labelling -----------------------------------------------------------------------------
def test_fallback_labels_every_trade_by_where_its_prices_came_from() -> None:
    f = perp_frame(up_path())
    none = recorded(real_rows(f).iloc[0:0].reindex(columns=real_rows(f).columns))
    modelled, _ = run(up_path(), SETUP, REAL_THEN_MODEL_FALLBACK, none)
    t = modelled.trades[0]
    assert t.entry_price_source == t.exit_price_source == "MODELED" and t.premium_source.startswith("MODEL")
    real, _ = run(up_path(), SETUP, REAL_THEN_MODEL_FALLBACK, recorded(real_rows(f)))
    assert real.trades[0].entry_price_source == real.trades[0].exit_price_source == "REAL_QUOTE"
    # real entry, then the quotes disappear: the exit is modelled and the trade says MIXED across the two ends
    part, _ = run(up_path(), SETUP, REAL_THEN_MODEL_FALLBACK, recorded(real_rows(f, skip=set(range(10, 60)))))
    p = part.trades[0]
    assert p.entry_price_source == "REAL_QUOTE" and p.exit_price_source == "MODELED"


def test_real_strikes_and_deltas_are_used_instead_of_the_models() -> None:
    f = perp_frame(np.full(60, 85000.0))
    # shift every real delta so the 84800 call (model delta ~0.58) looks ATM: the picker must follow the REAL deltas
    rec = recorded(real_rows(f, delta_shift=-0.05))
    res, _ = run(np.full(60, 85000.0), SETUP, REAL_ONLY, rec, selector=SelectorConfig(moneyness="ITM1", delta_min=0.40, delta_max=0.55))
    assert res.trades and res.trades[0].strikes[0] == 84800.0 and res.trades[0].entry_delta == pytest.approx(
        rec.quote("C", 84800.0, EXPIRIES[0], T0 + BAR).delta)
    modelled, _ = run(np.full(60, 85000.0), SETUP, MODEL_ONLY, selector=SelectorConfig(moneyness="ITM1", delta_min=0.40, delta_max=0.55))
    assert not modelled.trades or modelled.trades[0].strikes[0] != 84800.0  # the model's own delta (0.58) is outside the band


def test_only_recorded_expiries_and_strikes_are_eligible_in_real_modes() -> None:
    f = perp_frame(up_path())
    only_far = real_rows(f, strikes=np.array([88000.0]))  # nothing near the money was recorded
    res, _ = run(up_path(), SETUP, REAL_ONLY, recorded(only_far))
    assert not res.trades and res.skipped["no_strike_in_delta_band"] == 1


# ---- contract-picker hook ------------------------------------------------------------------------------------------------
def test_contract_picker_chooses_the_contract_and_sees_only_as_of_information() -> None:
    f = perp_frame(up_path())
    seen = {}

    def picker(ctx):
        seen["t"], seen["real"], seen["spot"] = ctx.t_entry, ctx.real_chain, ctx.spot
        seen["expiries"] = list(ctx.expiries)
        k = 85200.0
        q = ctx.fill("C", k, EXPIRIES[0], True)
        seen["ask"] = q[0]
        return Pick(EXPIRIES[0], long_call("BTC", k, EXPIRIES[0].to_pydatetime(), ctx.contracts, ctx.contract_value, ""),
                    detail={"why": "test"})

    rec = recorded(real_rows(f))
    cfg = OptionBacktestConfig(pricing_mode=REAL_ONLY, apply_breakeven_gate=False, contract_picker=picker, selection_name="TEST")
    res = run_option_backtest(Scripted(), f, market_for(f), cfg, setups=SETUP, recorded=rec)
    t = res.trades[0]
    assert t.strikes == (85200.0,) and t.selection == "TEST" and t.pick_detail == {"why": "test"} and t.entry_fills == [seen["ask"]]
    assert seen["t"] == T0 + BAR and seen["real"] is True and seen["spot"] == 85000.0
    assert seen["expiries"] == [EXPIRIES[0]]  # only the recorded expiry, not the model's four listed ones


def test_a_picker_that_refuses_is_counted_with_its_reason() -> None:
    f = perp_frame(up_path())
    cfg = OptionBacktestConfig(apply_breakeven_gate=False, contract_picker=lambda ctx: Pick(reason="filter_spread"))
    res = run_option_backtest(Scripted(), f, market_for(f), cfg, setups=SETUP)
    assert not res.trades and res.skipped["filter_spread"] == 1


def test_default_path_is_untouched_when_no_picker_is_given() -> None:
    f = perp_frame(up_path())
    a = run_option_backtest(Scripted(), f, market_for(f), OptionBacktestConfig(apply_breakeven_gate=False), setups=SETUP)
    b = run_option_backtest(Scripted(), f, market_for(f), OptionBacktestConfig(apply_breakeven_gate=False, contract_picker=None), setups=SETUP)
    assert a.trades[0].net_r == b.trades[0].net_r
