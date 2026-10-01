"""Selector, option market model and the hybrid option backtester on hand-built deterministic scenarios."""
from __future__ import annotations

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
from delta_intelligence.options.selector import build_structure, choose_expiry, option_symbol
from delta_intelligence.strategies.base import Setup, Strategy

T0 = pd.Timestamp("2026-09-01T00:00Z")
EXP1 = pd.Timestamp("2026-09-01T12:00Z")
EXP2 = pd.Timestamp("2026-09-02T12:00Z")
STRIKES = np.arange(80000, 90001, 200, dtype=float)


# ---- selector ------------------------------------------------------------------------------------------------------
def test_expiry_six_hour_rule() -> None:
    assert choose_expiry(pd.Timestamp("2026-09-01T06:00Z"), 6) == EXP1  # exactly 6 h left: allowed
    assert choose_expiry(pd.Timestamp("2026-09-01T06:05Z"), 6) == EXP2  # 5h55m: roll to tomorrow
    assert choose_expiry(pd.Timestamp("2026-09-01T13:00Z"), 6) == EXP2
    assert choose_expiry(pd.Timestamp("2026-09-01T01:00Z"), 6, listed={EXP2}) == EXP2  # unlisted day skipped


@pytest.mark.parametrize("policy,direction,expect", [
    ("LONG_OPTION", "LONG", [("C", 85000, 1)]),
    ("LONG_OPTION", "SHORT", [("P", 85000, 1)]),
    ("DEBIT_SPREAD", "LONG", [("C", 85000, 1), ("C", 85800, -1)]),
    ("DEBIT_SPREAD", "SHORT", [("P", 85000, 1), ("P", 84200, -1)]),
    ("CREDIT_SPREAD", "LONG", [("P", 84400, 1), ("P", 85000, -1)]),
    ("CREDIT_SPREAD", "SHORT", [("C", 85600, 1), ("C", 85200, -1)]),  # short call at/above spot 85040
])
def test_build_structure(policy, direction, expect) -> None:
    sign = 1 if direction == "LONG" else -1
    st = build_structure(policy, direction, "BTC", 85040.0, EXP1, STRIKES, stop=85040 - sign * 600,
                         target=85040 + sign * 800, contracts=10, contract_value=0.001,
                         symbol_for=lambda k, x, e: option_symbol(k, "BTC", x, e))
    assert [(l.kind, l.strike, l.side) for l in st.legs] == expect
    assert st.legs[0].symbol.endswith("-010926")


def test_build_structure_without_room_raises() -> None:
    with pytest.raises(ValueError):
        build_structure("DEBIT_SPREAD", "LONG", "BTC", 90000, EXP1, STRIKES, 89000, 91000, 1, 0.001)


# ---- market model ----------------------------------------------------------------------------------------------
def index_df(prices: np.ndarray, start=T0) -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(prices), freq="5min")
    return pd.DataFrame({"timestamp": ts, "open": prices, "high": prices, "low": prices, "close": prices})


def flat_iv(iv=0.5, start="2026-08-31T00:00Z", hours=72) -> pd.DataFrame:
    h = pd.date_range(start, periods=hours, freq="1h")
    return pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_0_6h": iv, "atm_iv_6_30h": iv,
                         "atm_iv_30_54h": iv})


def market(prices, iv=None, **kw) -> OptionMarketModel:
    return OptionMarketModel("BTC", index_df(prices), flat_iv() if iv is None else iv,
                             listed_strikes={EXP1: STRIKES, EXP2: STRIKES},
                             symbols={(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES
                                      for e in (EXP1, EXP2)}, **kw)


def test_iv_is_as_of_and_ages_out() -> None:
    iv = flat_iv().assign(atm_iv_6_30h=np.linspace(0.3, 0.9, 72))
    m = market(np.full(10, 85000.0), iv)
    at = pd.Timestamp("2026-09-01T03:30Z")  # latest AVAILABLE hour is 02:00 (available 03:00)
    expected = iv.loc[iv["available_at"] <= at, "atm_iv_6_30h"].iloc[-1]
    assert m.atm_iv(at, 10) == pytest.approx(expected)
    stale = OptionMarketModel("BTC", index_df(np.full(5, 85000.0)), iv.iloc[:2], max_iv_age_hours=3)
    assert stale.atm_iv(pd.Timestamp("2026-09-01T03:30Z"), 10) is None


def test_fills_are_worse_than_mid_and_tick_rounded() -> None:
    m = market(np.full(10, 85000.0))
    at = pd.Timestamp("2026-09-01T02:00Z")
    buy, mid, _ = m.fill("C", 85000, EXP1, at, 85000, buy=True)
    sell, _, _ = m.fill("C", 85000, EXP1, at, 85000, buy=False)
    assert sell < mid < buy
    assert abs(buy * 10 - round(buy * 10)) < 1e-9 and abs(sell * 10 - round(sell * 10)) < 1e-9


def test_settlement_twap() -> None:
    prices = np.full(160, 85000.0)
    prices[138:144] = 86000.0  # 11:30-11:55 bars
    assert market(prices).settlement_twap(EXP1) == pytest.approx(86000.0)


# ---- backtest --------------------------------------------------------------------------------------------------
class Scripted(Strategy):
    name = "scripted"
    default_structure = "LONG_OPTION"

    def raw_signals(self, f):  # not used: setups are passed explicitly
        return self.out(pd.Series(0, index=f.index), f["close"], f["close"])


def perp_frame(path: np.ndarray, start=pd.Timestamp("2026-09-01T01:00Z")) -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(path), freq="5min")
    return pd.DataFrame({"timestamp": ts, "open": path, "high": path + 20, "low": path - 20, "close": path,
                         "volume": 1.0, "index_close": path})


def run(path, setups, **cfg):
    f = perp_frame(path)
    m = OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], flat_iv(),
                          listed_strikes={EXP1: STRIKES, EXP2: STRIKES},
                          symbols={(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES for e in (EXP1, EXP2)})
    return run_option_backtest(Scripted(), f, m, OptionBacktestConfig(**cfg), setups=setups), f


def test_target_hit_long_call_wins() -> None:
    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 86500, 20)])
    res, f = run(path, [Setup(f_ts := pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84500, 86000)])
    t = res.trades[0]
    assert t.exit_reason == "TARGET" and t.net_pnl > 0 and t.net_r > 0
    assert t.expiry == EXP1 and t.structure == "LONG_CALL"  # 01:05 entry leaves 10h55m to today's expiry
    assert t.underlying_r == pytest.approx(2.0)


def test_stop_checked_before_target_and_loss_bounded() -> None:
    path = np.full(30, 85000.0)
    path[3] = 85000.0
    f = perp_frame(path)
    f.loc[3, "high"], f.loc[3, "low"] = 86500, 84000  # one bar spans both levels
    m = OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], flat_iv(),
                          listed_strikes={EXP1: STRIKES, EXP2: STRIKES})
    res = run_option_backtest(Scripted(), f, m, OptionBacktestConfig(),
                              setups=[Setup(f["timestamp"].iloc[0], "LONG", 85000, 84500, 86000)])
    t = res.trades[0]
    assert t.exit_reason == "STOP" and t.net_r < 0
    assert t.net_r >= -1.05  # a long option can't lose much more than its premium + fees


def test_settlement_guard_exits_before_1130_utc() -> None:
    path = np.full(200, 85000.0)
    res, f = run(path, [Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84000, 87000)], max_hold_bars=500)
    t = res.trades[0]
    assert t.exit_reason == "SETTLEMENT_GUARD" and t.exit_time <= EXP1 - pd.Timedelta("30min") + pd.Timedelta("5min")
    assert t.exit_time >= EXP1 - pd.Timedelta("30min")


def test_time_exit_and_no_overlap() -> None:
    path = np.full(120, 85000.0)
    s1 = Setup(pd.Timestamp("2026-09-01T01:00Z"), "LONG", 85000, 84000, 87000)
    s2 = Setup(pd.Timestamp("2026-09-01T01:30Z"), "SHORT", 85000, 86000, 83000)  # while s1 still open
    res, _ = run(path, [s1, s2], max_hold_bars=12)
    assert len(res.trades) == 1 and res.trades[0].exit_reason == "TIME" and res.skipped["overlap"] == 1
    assert res.trades[0].net_r < 0  # flat market: spread + fees + theta lose money


def test_missing_iv_skips_instead_of_inventing() -> None:
    f = perp_frame(np.full(30, 85000.0))
    m = OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], flat_iv(start="2026-09-05T00:00Z"),
                          listed_strikes={EXP1: STRIKES, EXP2: STRIKES})
    res = run_option_backtest(Scripted(), f, m, setups=[Setup(f["timestamp"].iloc[0], "LONG", 85000, 84500, 86000)])
    assert res.trades == [] and res.skipped["no_iv"] == 1


def test_validation_against_real_prices() -> None:
    path = np.concatenate([np.full(5, 85000.0), np.linspace(85000, 86500, 20)])
    entry_bar = pd.Timestamp("2026-09-01T01:00Z")
    sym = option_symbol("C", "BTC", 85000, EXP1)
    real = RealOptionPrices(lambda e: pd.DataFrame({"symbol": [sym], "timestamp": [entry_bar], "close": [600.0],
                                                    "volume": [3.0]}))
    f = perp_frame(path)
    m = OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], flat_iv(),
                          listed_strikes={EXP1: STRIKES, EXP2: STRIKES},
                          symbols={(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES for e in (EXP1,)})
    res = run_option_backtest(Scripted(), f, m, real=real, setups=[Setup(entry_bar, "LONG", 85000, 84500, 86000)])
    v = res.trades[0].validation
    assert v and v[0][0] == "entry" and v[0][3] == 600.0
    assert validation_stats(res.trades)["n"] == 1


def test_summaries_and_splits() -> None:
    path = np.full(400, 85000.0)
    setups = [Setup(pd.Timestamp("2026-09-01T01:00Z") + pd.Timedelta(minutes=5 * 20 * k), "LONG", 85000, 84000, 87000)
              for k in range(15)]
    res, _ = run(path, setups, max_hold_bars=10)
    s = summarize_trades(res.trades)
    assert s["n_trades"] == len(res.trades) and s["exit_reasons"]
    ins, oos = split_in_out(res.trades, 0.7)
    assert len(ins) + len(oos) == len(res.trades) and ins[-1].entry_time < oos[0].entry_time
    assert sum(len(f) for f in time_folds(res.trades, 4)) == len(res.trades)
