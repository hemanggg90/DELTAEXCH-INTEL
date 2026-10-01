"""Selector, premium model, option market model and the buying-only hybrid backtester on deterministic scenarios."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.backtesting.option_backtest import (
    OptionBacktestConfig,
    RealOptionPrices,
    run_option_backtest,
    split_in_out,
    summarize_trades,
    time_folds,
    validation_stats,
)
from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.options.chain import expiry_from_symbol
from delta_intelligence.options.premium_model import plan_premium
from delta_intelligence.options.selector import SelectorConfig, build_long, choose_expiry, option_symbol, pick_strike
from delta_intelligence.options.structures import long_call
from delta_intelligence.strategies.base import Setup, Strategy

T0 = pd.Timestamp("2026-09-01T00:00Z")
EXPIRIES = [pd.Timestamp(f"2026-09-0{d}T12:00Z") for d in (1, 2, 3, 4)]
STRIKES = np.arange(80000, 90001, 200, dtype=float)
CFG = SelectorConfig()


# ---- selector ------------------------------------------------------------------------------------------------------
def test_expiry_respects_dte_multiple_and_guard() -> None:
    now = pd.Timestamp("2026-09-01T02:00Z")  # 10 h to today's expiry
    assert choose_expiry(now, 2.0, EXPIRIES, CFG) == EXPIRIES[0]  # 10 >= 2.5*2 and 10-2 >= 2
    assert choose_expiry(now, 4.5, EXPIRIES, CFG) == EXPIRIES[1]  # 10 < 2.5*4.5 -> tomorrow
    assert choose_expiry(pd.Timestamp("2026-09-01T10:30Z"), 0.5, EXPIRIES, CFG) == EXPIRIES[1]  # 1.5h left < 2h guard
    assert choose_expiry(now, 48, EXPIRIES, CFG) is None  # nothing far enough
    far = [pd.Timestamp("2026-12-25T12:00Z")]
    assert choose_expiry(now, 2.0, far, CFG) is None  # first listed expiry months away: no trade


def test_xaut_expiry_is_2130_ist() -> None:
    e = expiry_from_symbol("C-XAUT-4250-011026")
    assert e == pd.Timestamp("2026-10-01T16:00Z") and e.tz_convert("Asia/Kolkata").strftime("%H:%M") == "21:30"
    assert expiry_from_symbol("C-BTC-85000-011026").tz_convert("Asia/Kolkata").strftime("%H:%M") == "17:30"


def test_pick_strike_delta_band_and_moneyness() -> None:
    deltas = {84800: 0.62, 85000: 0.52, 85200: 0.41}
    assert pick_strike("C", 85040, STRIKES, lambda k: deltas.get(k), CFG) == 85000
    assert pick_strike("C", 85040, STRIKES, lambda k: deltas.get(k), SelectorConfig(moneyness="ITM1")) == 85000  # 84800 delta .62 out of band
    deltas2 = {84800: 0.58, 85000: 0.52}
    assert pick_strike("C", 85040, STRIKES, lambda k: deltas2.get(k), SelectorConfig(moneyness="ITM1")) == 84800
    assert pick_strike("C", 85040, STRIKES, lambda k: 0.9, CFG) is None


def test_build_long_policies() -> None:
    e = EXPIRIES[1]
    sym = lambda k, x, ex: option_symbol(k, "BTC", x, ex)  # noqa: E731
    c = build_long("LONG_OPTION", "LONG", "BTC", 85040, e, STRIKES, 10, 0.001, lambda k, x: 0.5, CFG, sym)
    p = build_long("LONG_OPTION", "SHORT", "BTC", 85040, e, STRIKES, 10, 0.001, lambda k, x: 0.5, CFG, sym)
    sd = build_long("LONG_STRADDLE", "VOL", "BTC", 85040, e, STRIKES, 10, 0.001, lambda k, x: 0.5, CFG, sym)
    sg = build_long("LONG_STRANGLE", "VOL", "BTC", 85040, e, STRIKES, 10, 0.001, lambda k, x: 0.5, CFG, sym)
    assert (c.name, c.legs[0].kind, c.legs[0].symbol) == ("LONG_CALL", "C", "C-BTC-85000-020926")
    assert (p.name, p.legs[0].kind) == ("LONG_PUT", "P")
    assert [lg.strike for lg in sd.legs] == [85000, 85000] and [lg.strike for lg in sg.legs] == [85200, 84800]
    assert all(lg.side == 1 for s in (c, p, sd, sg) for lg in s.legs)
    with pytest.raises(ValueError):
        build_long("BULL_CALL_DEBIT", "LONG", "BTC", 85040, e, STRIKES, 1, 0.001, lambda k, x: 0.5, CFG)


# ---- premium model -----------------------------------------------------------------------------------------------
def test_premium_translation_and_breakeven_gate() -> None:
    now = dt.datetime(2026, 9, 1, 2, tzinfo=dt.timezone.utc)
    s = long_call("BTC", 85000, EXPIRIES[1].to_pydatetime(), 10, 0.001)
    fair = s.model_value(85000, now, [0.45]) / s.units(s.legs[0])
    ask = fair + 5
    big = plan_premium(s, 85000, now, [0.45], [ask], [5.0], 2.0, 84500, 88500, 4.0)
    assert big.premium_stop == pytest.approx(ask * 0.65)
    assert big.value_at_target > ask > big.value_at_underlying_stop
    assert big.passes_breakeven and big.expected_move == 3500
    small = plan_premium(s, 85000, now, [0.45], [ask], [5.0], 2.0, 84900, 85300, 4.0)
    assert not small.passes_breakeven and small.required_move == pytest.approx((ask + 5 + 2) * 1.25)
    assert big.approx_change_at_target == pytest.approx(big.value_at_target - fair, rel=0.35)  # greeks approx sane


def test_breakeven_uses_extrinsic_for_itm() -> None:
    now = dt.datetime(2026, 9, 1, 2, tzinfo=dt.timezone.utc)
    s = long_call("BTC", 84000, EXPIRIES[1].to_pydatetime(), 10, 0.001)
    plan = plan_premium(s, 85000, now, [0.45], [1300.0], [5.0], 2.0, 84500, 85800, 4.0)
    assert plan.required_move == pytest.approx((300 + 5 + 2) * 1.25)  # 1000 of the premium is intrinsic


# ---- market model --------------------------------------------------------------------------------------------------
def flat_iv(iv=0.5, start="2026-08-31T00:00Z", hours=120) -> pd.DataFrame:
    h = pd.date_range(start, periods=hours, freq="1h")
    return pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_0_6h": iv, "atm_iv_6_30h": iv,
                         "atm_iv_30_54h": iv})


def test_iv_is_as_of_and_ages_out() -> None:
    iv = flat_iv().assign(atm_iv_6_30h=np.linspace(0.3, 0.9, 120))
    idx = pd.DataFrame({"timestamp": pd.date_range(T0, periods=10, freq="5min"), "close": 85000.0})
    m = OptionMarketModel("BTC", idx, iv)
    at = pd.Timestamp("2026-09-01T03:30Z")
    assert m.atm_iv(at, 10) == pytest.approx(iv.loc[iv["available_at"] <= at, "atm_iv_6_30h"].iloc[-1])
    assert OptionMarketModel("BTC", idx, iv.iloc[:2]).atm_iv(at, 10) is None


# ---- backtest --------------------------------------------------------------------------------------------------
class Scripted(Strategy):
    name = "scripted"
    expected_hold_bars = 24
    max_hold_bars = 48

    def raw_signals(self, f):
        return self.out(pd.Series(0, index=f.index), f["close"], f["close"])


def perp_frame(path: np.ndarray, start=pd.Timestamp("2026-09-01T01:00Z")) -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(path), freq="5min")
    return pd.DataFrame({"timestamp": ts, "open": path, "high": path + 20, "low": path - 20, "close": path,
                         "volume": 1.0, "index_close": path, "index_realized_vol_1d": 0.5})


def market_for(f, iv=None) -> OptionMarketModel:
    syms = {(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES for e in EXPIRIES}
    return OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], flat_iv() if iv is None else iv,
                             listed_strikes={e: STRIKES for e in EXPIRIES}, symbols=syms)


def run(path, setups, strategy=None, **cfg):
    f = perp_frame(path)
    return run_option_backtest(strategy or Scripted(), f, market_for(f), OptionBacktestConfig(**cfg), setups=setups), f


def test_target_hit_long_call_wins_and_is_labelled() -> None:
    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 87500, 20)])
    res, _ = run(path, [Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84500, 87000)])
    t = res.trades[0]
    assert t.exit_reason == "TARGET" and t.net_r > 0 and t.structure == "LONG_CALL"
    assert t.premium_source == "MODEL_REAL_IV" and 0.4 <= t.entry_delta <= 0.6
    assert t.expiry == EXPIRIES[0]  # 01:05 entry: 10.9 h left >= 2.5 x 2 h hold


def test_loss_bounded_by_premium_and_stop_first() -> None:
    path = np.full(30, 85000.0)
    f = perp_frame(path)
    f.loc[3, "high"], f.loc[3, "low"] = 87500, 84000
    res = run_option_backtest(Scripted(), f, market_for(f),
                              setups=[Setup(f["timestamp"].iloc[0], "LONG", 85000, 84500, 87000)],
                              cfg=OptionBacktestConfig(apply_breakeven_gate=False))
    t = res.trades[0]
    assert t.exit_reason == "UNDERLYING_STOP" and -1.05 <= t.net_r < 0


def test_premium_stop_fires_on_decay() -> None:
    path = np.full(60, 85000.0)
    path[2:] = 84650.0  # drift down (no stop hit) -> call premium falls > 35%
    res, _ = run(path, [Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84000, 87000)],
                 apply_breakeven_gate=False)
    assert res.trades[0].exit_reason == "PREMIUM_STOP"


def test_expiry_guard_forces_exit() -> None:
    class Long(Scripted):
        expected_hold_bars, max_hold_bars = 6, 500

    path = np.full(200, 85000.0)
    res, _ = run(path, [Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84000, 87000)], strategy=Long(),
                 apply_breakeven_gate=False, premium_stop_pct=99.9)
    t = res.trades[0]
    assert t.exit_reason == "EXPIRY_GUARD" and t.exit_time >= EXPIRIES[0] - pd.Timedelta("2h")


def test_breakeven_gate_skips_small_moves() -> None:
    res, _ = run(np.full(40, 85000.0), [Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84950, 85060)])
    assert res.trades == [] and res.skipped["breakeven"] == 1


def test_missing_iv_skips_unless_rv_proxy() -> None:
    f = perp_frame(np.concatenate([np.full(5, 85000.0), np.linspace(85000, 87500, 20)]))
    m = market_for(f, iv=flat_iv(start="2026-09-10T00:00Z"))
    s = [Setup(f["timestamp"].iloc[0], "LONG", 85000, 84500, 87000)]
    res = run_option_backtest(Scripted(), f, m, setups=s)
    assert res.trades == [] and res.skipped["no_strike_in_delta_band"] + res.skipped["no_iv"] == 1
    res = run_option_backtest(Scripted(), f, m, OptionBacktestConfig(allow_rv_proxy=True), setups=s)
    assert res.trades and res.trades[0].premium_source == "MODEL_RV_PROXY"


def test_straddle_for_vol_view() -> None:
    class Event(Scripted):
        policy, is_event = "LONG_STRADDLE", True

    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 83000, 40)])
    res, _ = run(path, [Setup(pd.Timestamp("2026-09-01T01:00Z"), "VOL", 85000, 0.0, 0.0,
                              meta={"expected_abs_move": 2500})], strategy=Event())
    t = res.trades[0]
    assert t.structure == "LONG_STRADDLE" and len(t.entry_fills) == 2 and t.underlying_r == 0.0


def test_validation_against_real_prices_and_summaries() -> None:
    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 87500, 20)])
    entry_bar = pd.Timestamp("2026-09-01T01:00Z")
    sym = option_symbol("C", "BTC", 85000, EXPIRIES[0])
    real = RealOptionPrices(lambda e: pd.DataFrame({"symbol": [sym], "timestamp": [entry_bar], "close": [600.0],
                                                    "volume": [3.0]}))
    f = perp_frame(path)
    res = run_option_backtest(Scripted(), f, market_for(f), real=real, setups=[Setup(entry_bar, "LONG", 85000, 84500, 87000)])
    assert validation_stats(res.trades)["n"] == 1
    s = summarize_trades(res.trades)
    assert s["premium_sources"] == {"MODEL_REAL_IV": 1}
    ins, oos = split_in_out(res.trades)
    assert len(ins) + len(oos) == 1 and sum(len(x) for x in time_folds(res.trades)) == 1
