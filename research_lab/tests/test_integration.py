"""Integration with the project's pipeline: real feature frames, no look-ahead by truncation, 15m resampling, the
standardised signal / NO SIGNAL / RISK VETO outcomes, and option selection + costs through the project's backtester."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.backtesting.option_backtest import OptionBacktestConfig, run_option_backtest
from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.options.selector import SelectorConfig, option_symbol
from delta_intelligence.options.structures import leg_fee
from helpers import mini_frame, with_close
from lab import runner
from lab.frame import build_lab_frame, map_setups, resample_ohlcv, to_base_timestamp
from lab.library import VARIANTS
from lab.signal import project_risk_check
from lab.strategies import STRATEGY_CLASSES, DonchianBreakout, LongStraddleExpansion
from lab import config as C
from synth import iv_hourly, make_inputs


@pytest.fixture(scope="module")
def inputs():
    return make_inputs(days=45, seed=33)


@pytest.fixture(scope="module")
def frames(inputs):
    o, aux = inputs
    iv = iv_hourly(o)
    return {tf: build_lab_frame(o, aux, tf, iv, with_regime=True) for tf in ("5m", "15m")}


# ---- frame ---------------------------------------------------------------------------------------------------------
def test_frame_has_every_column_the_strategies_need(frames) -> None:
    for tf, f in frames.items():
        for cls in STRATEGY_CLASSES:
            missing = [c for c in cls.required if c not in f]
            assert not missing, (cls.name, tf, missing)
        assert f["regime"].notna().all() and len(f) > 1000


def test_resample_keeps_only_complete_buckets_and_aggregates_correctly() -> None:
    ts = pd.date_range("2026-03-02T00:00Z", periods=8, freq="5min")  # 00:00..00:35 -> two full 15m + a partial
    df = pd.DataFrame({"timestamp": ts, "open": np.arange(8) + 100.0, "high": np.arange(8) + 101.0,
                       "low": np.arange(8) + 99.0, "close": np.arange(8) + 100.5, "volume": 1.0})
    out = resample_ohlcv(df, "15m")
    assert len(out) == 2  # 00:30 bucket has only 2 of 3 five-minute bars: dropped, never half-built
    first = out.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (100.0, 103.0, 99.0, 102.5, 3.0)
    gap = df.drop(index=[1])
    assert len(resample_ohlcv(gap, "15m")) == 1  # a bucket with a missing bar is dropped


def test_15m_setup_is_retimed_to_the_last_5m_bar_of_the_signal_bar(frames) -> None:
    f5, f15 = frames["5m"], frames["15m"]
    s = DonchianBreakout(C.DonchianConfig(n=20), "15m")
    setups = s.historical_setups(f15)
    assert setups
    mapped = map_setups(setups, "15m")
    pos = {t: i for i, t in enumerate(f5["timestamp"])}
    for a, b in zip(setups[:20], mapped[:20]):
        assert b.timestamp == a.timestamp + pd.Timedelta(minutes=10) == to_base_timestamp(a.timestamp, "15m")
        assert f5["close"].iloc[pos[b.timestamp]] == pytest.approx(a.entry_price)  # same instant, same price
    assert map_setups(setups, "5m") is setups


# ---- no look-ahead ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tf", ["5m", "15m"])
def test_signals_never_use_future_bars(inputs, frames, tf) -> None:
    """Build the frame from truncated raw data and from all of it; every strategy's signals (and their stop/target)
    must agree on the rows both contain."""
    o, aux = inputs
    iv = iv_hourly(o)
    full = frames[tf]
    for cut in (288 * 24 + 36, 288 * 33 + 120):
        part = build_lab_frame(o.iloc[:cut], aux, tf, iv, with_regime=False)
        k = len(part)
        assert 0 < k < len(full)
        for v in VARIANTS:
            s = v.build(tf)
            a, b = s.signals(part), s.signals(full).iloc[:k]
            assert (a["direction"].to_numpy() == b["direction"].to_numpy()).all(), (v.id, tf, cut)
            m = a["direction"].to_numpy() != 0
            assert np.allclose(a["stop"].to_numpy()[m], b["stop"].to_numpy()[m], equal_nan=True), (v.id, tf)
            assert np.allclose(a["target"].to_numpy()[m], b["target"].to_numpy()[m], equal_nan=True), (v.id, tf)


def test_no_signal_during_warmup_or_where_an_input_is_missing(frames) -> None:
    f = frames["5m"]
    for v in VARIANTS:
        s = v.build("5m")
        d = s.signals(f.iloc[:13].reset_index(drop=True))["direction"]
        assert (d == 0).all(), v.id  # ATR(14) does not exist yet: nothing can signal
        full = s.signals(f)["direction"].to_numpy() != 0
        ok = f[list(s.required)].notna().all(axis=1).to_numpy()
        assert not (full & ~ok).any(), v.id  # a signal never fires on a bar with a missing required input


def test_strategies_fire_on_realistic_frames_with_valid_geometry(frames) -> None:
    fired = set()
    for v in VARIANTS:
        s = v.build("5m")
        sig = s.signals(frames["5m"])
        for i in np.flatnonzero(sig["direction"].to_numpy()):
            d, c = sig["direction"].iloc[i], frames["5m"]["close"].iloc[i]
            if d == 1:
                assert sig["stop"].iloc[i] < c < sig["target"].iloc[i]
            elif d == -1:
                assert sig["target"].iloc[i] < c < sig["stop"].iloc[i]
            else:
                assert d == 2 and s.is_vol
            fired.add(v.strategy_name)
    assert len(fired) >= 8  # the synthetic series has no events or regimes designed for any one strategy


# ---- standardised signal and outcomes -----------------------------------------------------------------------------
def _last_signal_frame(frames, vid: str = "S2-n20"):
    f = frames["5m"]
    s = next(v for v in VARIANTS if v.id == vid).build("5m")
    d = s.signals(f)["direction"].to_numpy()
    i = int(np.flatnonzero(d)[5])
    return s, f.iloc[: i + 1].reset_index(drop=True), f.iloc[: i].reset_index(drop=True)


def test_standardised_signal_fields(frames) -> None:
    s, f, _ = _last_signal_frame(frames)
    out = s.evaluate_now(f, "BTC")
    assert out.kind == "SIGNAL" and out.signal is not None
    g = out.signal
    assert (g.strategy, g.version, g.asset, g.timeframe) == (s.name, "1.0", "BTC", "5m")
    assert g.direction in ("CALL", "PUT") and 0 <= g.strength <= 1 and g.regime != ""
    assert g.timestamp == f["timestamp"].iloc[-1] and g.decision_time == g.timestamp + pd.Timedelta(minutes=5)
    assert g.underlying_price == f["close"].iloc[-1]
    assert g.stop_price is not None and g.target_price is not None
    for text in (g.entry_condition, g.invalidation_condition, g.stop_logic, g.target_logic):
        assert text and "{" not in text  # templates fully rendered
    assert g.confidence["parameters"]["n"] == 20 and g.confidence["policy"] == "LONG_OPTION"


def test_straddle_signal_is_marked_and_has_no_stop(frames) -> None:
    f = frames["5m"]
    s = LongStraddleExpansion(C.StraddleConfig(rv_pct_max=0.4, atr_pct_max=0.4, bbw_pct_max=0.4, cooldown_bars=1))
    sigs = s.research_signals(f, "ETH")
    assert sigs
    g = sigs[0]
    assert g.direction == "STRADDLE" and g.stop_price is None and g.target_price is None
    assert g.confidence["expected_abs_move"] > 0


def test_no_signal_is_a_first_class_result_with_a_reason(frames) -> None:
    s, _, before = _last_signal_frame(frames)
    d = s.signals(before)["direction"].iloc[-1]
    out = s.evaluate_now(before, "BTC")
    assert (out.kind == "NO_SIGNAL") == (d == 0) and out.signal is None
    if d == 0:
        assert "no valid setup" in out.reason
    warm = before.iloc[:20].reset_index(drop=True)
    w = s.evaluate_now(warm, "BTC")
    assert w.kind == "NO_SIGNAL" and "missing data or warm-up" in w.reason
    assert s.evaluate_now(before.iloc[:0], "BTC").kind == "NO_SIGNAL"


def test_risk_veto_carries_the_exact_reason_and_approval_passes_through(frames) -> None:
    s, f, _ = _last_signal_frame(frames)
    seen = []

    def veto(signal, setup):
        seen.append((signal.direction, setup.direction))
        return False, "premium per trade 0.9% > 0.5% of equity"

    out = s.evaluate_now(f, "BTC", risk_check=veto)
    assert out.kind == "RISK_VETO" and out.reason == "premium per trade 0.9% > 0.5% of equity"
    assert out.signal is not None and seen  # the valid signal is still reported next to the veto
    assert s.evaluate_now(f, "BTC", risk_check=lambda sig, st: (True, "")).kind == "SIGNAL"


def test_project_risk_adapter_passes_the_planner_and_risk_engine_verdicts(frames, monkeypatch) -> None:
    from types import SimpleNamespace

    import delta_intelligence.execution.planner as planner
    import delta_intelligence.risk.risk_engine as risk

    s, f, _ = _last_signal_frame(frames)
    calls = {}
    monkeypatch.setattr(planner, "plan_trade", lambda *a, **k: SimpleNamespace(ok=False, reason="no listed expiry fits"))
    chk = project_risk_check(strategy=s, moneyness="ATM", underlying=None, chain=None, index_spot=1.0, perp_close=1.0,
                             now=None, settings=None, account={}, account_state=None)
    assert s.evaluate_now(f, "BTC", risk_check=chk).reason == "no listed expiry fits"

    def fake_eval(account_state, trade, persist=True):
        calls["persist"] = persist
        return SimpleNamespace(approved=False, reason="daily loss limit reached")

    monkeypatch.setattr(planner, "plan_trade", lambda *a, **k: SimpleNamespace(ok=True, reason="", proposed="P"))
    monkeypatch.setattr(risk, "evaluate_trade", fake_eval)
    chk = project_risk_check(strategy=s, moneyness="ATM", underlying=None, chain=None, index_spot=1.0, perp_close=1.0,
                             now=None, settings=None, account={}, account_state="A")
    out = s.evaluate_now(f, "BTC", risk_check=chk)
    assert out.kind == "RISK_VETO" and out.reason == "daily loss limit reached"
    assert calls["persist"] is False  # research never writes risk events


# ---- option selection + costs through the project's backtester ------------------------------------------------------
T0 = "2026-09-01T01:00Z"
EXPIRIES = [pd.Timestamp(f"2026-09-0{d}T12:00Z") for d in (1, 2, 3, 4)]
STRIKES = np.arange(80000, 90001, 200, dtype=float)


def _market(f):
    h = pd.date_range("2026-08-31T00:00Z", periods=120, freq="1h")
    iv = pd.DataFrame({"hour": h, "available_at": h + pd.Timedelta("1h"), "atm_iv_0_6h": 0.5, "atm_iv_6_30h": 0.5,
                       "atm_iv_30_54h": 0.5})
    syms = {(k, s, e): option_symbol(k, "BTC", s, e) for k in "CP" for s in STRIKES for e in EXPIRIES}
    return OptionMarketModel("BTC", f[["timestamp", "open", "high", "low", "close"]], iv,
                             listed_strikes={e: STRIKES for e in EXPIRIES}, symbols=syms)


def _btc_frame(n=80, **over):
    return mini_frame(n, start=T0, price=85000.0, **{"atr_14": 150.0, **over})


def _asset(f):
    return runner.AssetData("BTC", "BTCUSD", {"5m": f}, _market(f))


def test_directional_signal_buys_the_actual_call_contract_and_pays_its_costs() -> None:
    n = 80
    close = np.full(n, 85000.0)
    close[40:] = np.linspace(86000, 88000, n - 40)
    f = with_close(_btc_frame(n), close, wick=20.0)
    f["index_close"] = f["close"]
    s = DonchianBreakout(C.DonchianConfig(n=20, cooldown_bars=1), "5m")
    res = runner.backtest(_asset(f), s, "5m", SelectorConfig(), apply_breakeven_gate=False)
    assert res.trades, res.skipped
    t = res.trades[0]
    assert t.structure == "LONG_CALL" and len(t.entry_fills) == 1 and 0.4 <= t.entry_delta <= 0.6
    assert t.expiry == EXPIRIES[0]  # 2 h hold needs >= 5 h to expiry: today's 12:00 expiry qualifies
    assert t.fees > 0 and t.max_loss > 0 and t.net_r > -1.05  # loss bounded by premium plus fees
    spot_in, spot_out = f["index_close"].iloc[t.entry_idx], f["index_close"].iloc[t.exit_idx]
    expected_fees = (leg_fee(t.entry_fills[0], spot_in, 1.0, 0.0001, 0.035, 0.18)
                     + leg_fee(t.exit_fills[0], spot_out, 1.0, 0.0001, 0.035, 0.18))
    assert t.fees == pytest.approx(expected_fees, rel=0.35)  # charged on the real option legs (exit spot may be the level)


def test_put_is_bought_for_a_downside_signal() -> None:
    n = 80
    close = np.full(n, 85000.0)
    close[40:] = np.linspace(84000, 82000, n - 40)
    f = with_close(_btc_frame(n), close, wick=20.0)
    f["index_close"] = f["close"]
    s = DonchianBreakout(C.DonchianConfig(n=20, cooldown_bars=1), "5m")
    res = runner.backtest(_asset(f), s, "5m", SelectorConfig(), apply_breakeven_gate=False)
    assert res.trades and res.trades[0].structure == "LONG_PUT"


def test_straddle_costs_apply_to_both_legs() -> None:
    n = 100
    low = np.full(n, 0.9)
    low[40:] = 0.05
    close = np.full(n, 85000.0)
    close[60:] = 85000.0 + np.linspace(0, 2500, n - 60)
    f = with_close(_btc_frame(n, rv_pct=low, atr_pct=low, bbw_pct=low), close, wick=20.0)
    f["index_close"] = f["close"]
    s = LongStraddleExpansion(C.StraddleConfig(cooldown_bars=1, move_mult=20.0), "5m")
    assert s.policy == "LONG_STRADDLE"
    res = runner.backtest(_asset(f), s, "5m", SelectorConfig(), apply_breakeven_gate=False)
    assert res.trades, res.skipped
    t = res.trades[0]
    assert t.structure == "LONG_STRADDLE" and len(t.entry_fills) == 2 and len(t.exit_fills) == 2
    assert len(set(t.strikes)) == 1  # ATM call and put at the same strike
    spot_in, spot_out = f["index_close"].iloc[t.entry_idx], f["index_close"].iloc[t.exit_idx]
    expected = sum(leg_fee(p, spot_in, 1.0, 0.0001, 0.035, 0.18) for p in t.entry_fills) \
        + sum(leg_fee(p, spot_out, 1.0, 0.0001, 0.035, 0.18) for p in t.exit_fills)
    assert t.fees == pytest.approx(expected)  # four fills, each charged
    assert t.max_loss == pytest.approx(sum(t.entry_fills) + sum(
        leg_fee(p, spot_in, 1.0, 0.0001, 0.035, 0.18) for p in t.entry_fills))  # both premiums + entry fees


def test_expected_move_of_a_straddle_scales_with_assumed_expansion() -> None:
    f = _btc_frame(10)
    a = LongStraddleExpansion(C.StraddleConfig(move_mult=1.0)).expected_abs_move(f, 5)
    b = LongStraddleExpansion(C.StraddleConfig(move_mult=2.0)).expected_abs_move(f, 5)
    assert b == pytest.approx(2 * a) and a > 0


def test_the_breakeven_gate_can_veto_an_underpowered_setup() -> None:
    n = 80
    close = np.full(n, 85000.0)
    close[40:] = np.linspace(85080, 85200, n - 40)  # tiny breakout: expected move far below premium + costs
    f = with_close(_btc_frame(n, atr_14=20.0), close, wick=5.0)
    f["index_close"] = f["close"]
    s = DonchianBreakout(C.DonchianConfig(n=20, cooldown_bars=1), "5m")
    res = runner.backtest(_asset(f), s, "5m", SelectorConfig(), apply_breakeven_gate=True)
    assert res.skipped["breakeven"] >= 1 and not res.trades


def test_buckets_change_the_contract_not_the_signals() -> None:
    n = 80
    close = np.full(n, 85000.0)
    close[40:] = np.linspace(86000, 88000, n - 40)
    f = with_close(_btc_frame(n), close, wick=20.0)
    f["index_close"] = f["close"]
    s = DonchianBreakout(C.DonchianConfig(n=20, cooldown_bars=1), "5m")
    setups = s.historical_setups(f)
    atm = runner.backtest(_asset(f), s, "5m", SelectorConfig(moneyness="ATM"), setups=setups, apply_breakeven_gate=False)
    itm = runner.backtest(_asset(f), s, "5m", SelectorConfig(moneyness="ITM1", delta_min=0.50, delta_max=0.70),
                          setups=setups, apply_breakeven_gate=False)
    assert atm.n_setups == itm.n_setups == len(setups)
    if atm.trades and itm.trades:
        assert itm.trades[0].strikes[0] <= atm.trades[0].strikes[0]  # a call, one strike in the money or the same


def test_the_projects_own_supertrend_runs_through_the_same_pipeline(frames) -> None:
    from delta_intelligence.strategies.legacy_v2_library import SupertrendFlip

    st = SupertrendFlip({"target_atr_mult": 2.25})
    setups = st.historical_setups(frames["5m"])
    assert isinstance(setups, list)  # the legacy class reads columns from the lab frame unchanged
    strat, sel, tf = runner.build_for("LEGACY-ST-tight-ITM1", "15m")
    assert tf == "5m" and sel.moneyness == "ITM1" and isinstance(strat, SupertrendFlip)
    assert strat.parameters["target_atr_mult"] == 2.25
