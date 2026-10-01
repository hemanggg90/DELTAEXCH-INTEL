"""
Hybrid backtest for a BUYING-ONLY options system: signals on the underlying, P&L as bought option premium.

For each setup (one trade at a time per strategy):

1. **Entry**, at the signal bar's CLOSE:
   - expiry: the nearest listed expiry with TTE >= dte_multiple × the strategy's expected hold, whose hold ends
     before the expiry guard;
   - strike: ATM (or 1-ITM) call for LONG, put for SHORT, with |model delta| in [0.40, 0.60]; straddle/strangle for
     volatility strategies;
   - legs bought at the modelled ASK.
2. **Breakeven gate:** skip if the expected move < (extrinsic + exit spread + fees) × (1 + margin).
3. **Exits**, the first of:
   - the underlying stop (invalidation; checked before the target within a bar);
   - the underlying target;
   - the premium stop (structure bid value <= entry cost × (1 − 35%));
   - the strategy's time stop;
   - a forced exit before the expiry guard.

   Legs are sold at the modelled BID.
4. **R** = net USD P&L / (premium paid + entry fees). That is the max loss of a bought option, and it is what sizing
   uses.

**Premium source label per trade:**
- `MODEL_REAL_IV`: Black-Scholes at the as-of IV inferred from real Delta option trades;
- `MODEL_RV_PROXY`: no IV observed within the age limit, so the realised volatility of the index stands in (only if
  `allow_rv_proxy`).

Validation compares the model mid with real traded prices in the same contract and bar wherever they exist.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.options.premium_model import plan_premium
from delta_intelligence.options.pricing import bs_price, greeks, year_fraction
from delta_intelligence.options.selector import SelectorConfig, build_long, choose_expiry
from delta_intelligence.options.structures import leg_fee
from delta_intelligence.strategies.base import Setup, Strategy

BAR = pd.Timedelta(minutes=5)
CONTRACT_VALUE = {"BTC": 0.001, "ETH": 0.01, "XAUT": 0.001}  # verified 2026-10-02


@dataclass
class OptionBacktestConfig:
    policy: str | None = None  # None = the strategy's policy
    selector: SelectorConfig = field(default_factory=SelectorConfig)
    premium_stop_pct: float = 35.0
    breakeven_margin: float = 0.25
    apply_breakeven_gate: bool = True
    allow_rv_proxy: bool = False
    units: float = 1.0  # underlying units per leg (R is size-invariant up to tick/fee rounding)
    commission_rate: float = 0.0001
    premium_cap_rate: float = 0.035
    gst_rate: float = 0.18


@dataclass
class OptionTrade:
    strategy: str
    structure: str
    direction: str
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    exit_reason: str
    expiry: pd.Timestamp
    strikes: tuple
    entry_fills: list
    exit_fills: list
    entry_iv: float
    fees: float
    max_loss: float  # premium paid + entry fees (R denominator)
    net_pnl: float
    net_r: float
    underlying_r: float
    premium_source: str = "MODEL_REAL_IV"
    entry_delta: float | None = None
    validation: list = field(default_factory=list)

    @property
    def win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class OptionBacktestResult:
    strategy: str
    underlying: str
    trades: list[OptionTrade]
    n_setups: int
    skipped: Counter

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([{k: getattr(t, k) for k in (
            "strategy", "structure", "direction", "entry_idx", "exit_idx", "entry_time", "exit_time", "exit_reason",
            "expiry", "entry_iv", "fees", "max_loss", "net_pnl", "net_r", "underlying_r", "premium_source")}
            for t in self.trades])


class RealOptionPrices:
    """Real traded 5m bars (volume > 0) per symbol, for validation. `loader(expiry)` returns that expiry's frame."""

    def __init__(self, loader):
        self.loader = loader
        self._by_expiry: dict = {}

    def price(self, symbol: str, expiry: pd.Timestamp, bar_open: pd.Timestamp) -> float | None:
        if expiry not in self._by_expiry:
            df = self.loader(expiry)
            if df is None or not len(df):
                self._by_expiry[expiry] = {}
            else:
                df = df[df["volume"].fillna(0) > 0]
                self._by_expiry[expiry] = {(s, pd.Timestamp(t)): float(c) for s, t, c in
                                           zip(df["symbol"], pd.to_datetime(df["timestamp"], utc=True), df["close"])}
        return self._by_expiry[expiry].get((symbol, pd.Timestamp(bar_open)))


def run_option_backtest(strategy: Strategy, frame: pd.DataFrame, market: OptionMarketModel,
                        cfg: OptionBacktestConfig | None = None, real: RealOptionPrices | None = None,
                        setups: list[Setup] | None = None) -> OptionBacktestResult:
    cfg = cfg or OptionBacktestConfig()
    policy = cfg.policy or strategy.policy
    sel = cfg.selector
    setups = setups if setups is not None else strategy.historical_setups(frame)
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    ts_list, close_t = list(ts), list(ts + BAR)
    pos = {t: i for i, t in enumerate(ts_list)}
    hi, lo, close = frame["high"].to_numpy(), frame["low"].to_numpy(), frame["close"].to_numpy()
    idx_close = frame["index_close"].to_numpy() if "index_close" in frame else np.full(len(frame), np.nan)
    rv = frame["index_realized_vol_1d"].to_numpy() if "index_realized_vol_1d" in frame else np.full(len(frame), np.nan)
    expiries = sorted(market.listed_strikes)
    cv = CONTRACT_VALUE.get(market.underlying, 0.001)
    contracts = max(1, int(round(cfg.units / cv)))
    hold_h = strategy.expected_hold_bars * 5 / 60.0
    skipped: Counter = Counter()
    trades: list[OptionTrade] = []
    next_free = 0

    def leg_iv(at, kstrike, expiry, spot, i):
        t_h = (expiry - at).total_seconds() / 3600.0
        iv = market.leg_iv(at, t_h, kstrike, spot)
        if iv is None and cfg.allow_rv_proxy and np.isfinite(rv[i]) and rv[i] > 0:
            return float(rv[i]), "MODEL_RV_PROXY"
        return iv, "MODEL_REAL_IV"

    def fill(leg_kind, k, expiry, at, spot, buy, i):
        iv, src = leg_iv(at, k, expiry, spot, i)
        if iv is None:
            return None
        t = year_fraction(max(0.0, (expiry - at).total_seconds()))
        mid = float(bs_price(spot, k, t, iv, leg_kind))
        h = max(market.half_spread(k, spot) * mid, market.tick / 2)
        px = np.ceil((mid + h) / market.tick - 1e-9) * market.tick if buy else \
            max(np.floor((mid - h) / market.tick + 1e-9) * market.tick, 0.0)
        return float(px), mid, iv, src

    for s in setups:
        i = pos.get(pd.Timestamp(s.timestamp))
        if i is None:
            continue
        if i < next_free:
            skipped["overlap"] += 1
            continue
        spot = idx_close[i]
        if not np.isfinite(spot):
            skipped["no_index"] += 1
            continue
        t_entry = close_t[i]
        expiry = choose_expiry(t_entry, hold_h, expiries, sel)
        if expiry is None:
            skipped["no_expiry_for_hold"] += 1
            continue
        basis = spot / close[i]
        strikes = market.listed_strikes.get(expiry, np.array([]))

        def abs_delta(kind, k, _e=expiry, _i=i, _spot=spot, _t=t_entry):
            iv, _ = leg_iv(_t, k, _e, _spot, _i)
            if iv is None:
                return None
            return abs(float(greeks(_spot, k, year_fraction((_e - _t).total_seconds()), iv, kind)["delta"]))

        try:
            st = build_long(policy, s.direction, market.underlying, spot, expiry, strikes, contracts, cv, abs_delta, sel,
                            symbol_for=lambda k, x, e: market.symbols.get((k, x, e), ""))
        except ValueError:
            skipped["no_strike_in_delta_band"] += 1
            continue
        entry = [fill(leg.kind, leg.strike, expiry, t_entry, spot, True, i) for leg in st.legs]
        if any(e is None for e in entry):
            skipped["no_iv"] += 1
            continue
        source = "MODEL_RV_PROXY" if any(e[3] == "MODEL_RV_PROXY" for e in entry) else "MODEL_REAL_IV"
        units = [st.units(leg) for leg in st.legs]
        entry_px = [e[0] for e in entry]
        fee_in = sum(leg_fee(p, spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                     for p, u in zip(entry_px, units))
        paid = st.premium_paid(entry_px)
        max_loss = paid + fee_in
        if cfg.apply_breakeven_gate:
            half_spreads = [e[0] - e[1] for e in entry]  # exit spread assumed equal to the entry half-spread
            fees_per_unit = 2 * fee_in / units[0] if units[0] else 0.0  # round trip, per underlying unit
            plan = plan_premium(st, spot, t_entry.to_pydatetime(), [e[2] for e in entry], entry_px, half_spreads,
                                fees_per_unit, s.stop_price * basis, s.target_price * basis, hold_h,
                                cfg.premium_stop_pct, cfg.breakeven_margin,
                                expected_abs_move=s.meta.get("expected_abs_move"))
            if not plan.passes_breakeven:
                skipped["breakeven"] += 1
                continue
        stop_value = paid * (1 - cfg.premium_stop_pct / 100.0)
        guard = expiry - pd.Timedelta(hours=sel.expiry_guard_hours)
        j_exit, reason, level = None, None, None
        last = min(len(frame) - 1, i + strategy.max_hold_bars)
        every = max(1, int(strategy.premium_check_every))
        for j in range(i + 1, last + 1):
            if s.direction == "LONG":
                hit_stop, hit_tgt = lo[j] <= s.stop_price, hi[j] >= s.target_price
            elif s.direction == "SHORT":
                hit_stop, hit_tgt = hi[j] >= s.stop_price, lo[j] <= s.target_price
            else:  # VOL (straddle/strangle): no underlying levels
                hit_stop = hit_tgt = False
            if hit_stop:
                j_exit, reason, level = j, "UNDERLYING_STOP", s.stop_price
            elif hit_tgt:
                j_exit, reason, level = j, "TARGET", s.target_price
            elif close_t[j] >= guard:
                j_exit, reason = j, "EXPIRY_GUARD"
            elif j == last:
                j_exit, reason = j, "TIME_STOP" if j == i + strategy.max_hold_bars else "END_OF_DATA"
            elif (j - i) % every == 0 and np.isfinite(idx_close[j]):
                bids = [fill(leg.kind, leg.strike, expiry, close_t[j], idx_close[j], False, j) for leg in st.legs]
                if all(b is not None for b in bids) and sum(b[0] * u for b, u in zip(bids, units)) <= stop_value:
                    j_exit, reason = j, "PREMIUM_STOP"
            if j_exit is not None:
                break
        if j_exit is None:
            skipped["no_exit_bar"] += 1
            continue
        t_exit = close_t[j_exit]
        if not np.isfinite(idx_close[j_exit]):
            skipped["no_index"] += 1
            continue
        exit_spot = level * idx_close[j_exit] / close[j_exit] if level is not None else idx_close[j_exit]
        ex = [fill(leg.kind, leg.strike, expiry, t_exit, exit_spot, False, j_exit) for leg in st.legs]
        if any(e is None for e in ex):
            skipped["no_iv_exit"] += 1
            continue
        exit_px = [e[0] for e in ex]
        fee_out = sum(leg_fee(p, exit_spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                      for p, u in zip(exit_px, units))
        net = sum((xp - ep) * u for ep, xp, u in zip(entry_px, exit_px, units)) - fee_in - fee_out
        if s.direction in ("LONG", "SHORT") and s.risk > 0:
            under_exit = level if level is not None else close[j_exit]
            under_r = (1 if s.direction == "LONG" else -1) * (under_exit - s.entry_price) / s.risk
        else:
            under_r = 0.0
        validation = []
        if real is not None:
            for when, bar_i, fills in (("entry", i, entry), ("exit", j_exit, ex)):
                for leg, f in zip(st.legs, fills):
                    p = real.price(leg.symbol, expiry, ts_list[bar_i]) if leg.symbol else None
                    if p is not None:
                        validation.append((when, leg.symbol, f[1], p))
        d0 = abs_delta(st.legs[0].kind, st.legs[0].strike)
        trades.append(OptionTrade(
            strategy=strategy.name, structure=st.name, direction=s.direction, entry_idx=i, exit_idx=j_exit,
            entry_time=t_entry, exit_time=t_exit, exit_reason=reason, expiry=expiry,
            strikes=tuple(leg.strike for leg in st.legs), entry_fills=entry_px, exit_fills=exit_px,
            entry_iv=float(entry[0][2]), fees=fee_in + fee_out, max_loss=max_loss, net_pnl=net,
            net_r=net / max_loss, underlying_r=under_r, premium_source=source, entry_delta=d0, validation=validation))
        next_free = j_exit + 1
    return OptionBacktestResult(strategy.name, market.underlying, trades, len(setups), skipped)


# ---- metrics ---------------------------------------------------------------------------------------------------------
def max_drawdown_r(net_r: np.ndarray) -> float | None:
    if len(net_r) == 0:
        return None
    eq = np.cumsum(net_r)
    return float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())


def summarize_trades(trades: list[OptionTrade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    r = np.array([t.net_r for t in trades])
    u = np.array([t.underlying_r for t in trades])
    days = pd.Series([t.entry_time.floor("D") for t in trades])
    return {
        "n_trades": len(trades), "n_days": int(days.nunique()),
        "win_rate": float((r > 0).mean()), "net_expected_r": float(r.mean()), "median_net_r": float(np.median(r)),
        "total_net_r": float(r.sum()), "max_drawdown_r": max_drawdown_r(r),
        "underlying_expected_r": float(u.mean()),
        "avg_fees_share_of_risk": float(np.mean([t.fees / t.max_loss for t in trades])),
        "avg_hold_bars": float(np.mean([t.exit_idx - t.entry_idx for t in trades])),
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
        "premium_sources": dict(Counter(t.premium_source for t in trades)),
    }


def validation_stats(trades: list[OptionTrade]) -> dict:
    rows = [(w, m, p) for t in trades for (w, _, m, p) in t.validation if p > 0]
    if not rows:
        return {"n": 0}
    m = np.array([r[1] for r in rows])
    p = np.array([r[2] for r in rows])
    rel = (m - p) / p
    return {"n": len(rows), "median_abs_err_pct": float(np.median(np.abs(rel)) * 100),
            "median_bias_pct": float(np.median(rel) * 100), "p90_abs_err_pct": float(np.percentile(np.abs(rel), 90) * 100)}


def split_in_out(trades: list[OptionTrade], in_sample_frac: float = 0.7) -> tuple[list, list]:
    if not trades:
        return [], []
    cut = trades[0].entry_time + (trades[-1].entry_time - trades[0].entry_time) * in_sample_frac
    return [t for t in trades if t.entry_time <= cut], [t for t in trades if t.entry_time > cut]


def time_folds(trades: list[OptionTrade], n_folds: int = 4) -> list[list[OptionTrade]]:
    if not trades:
        return []
    t0, t1 = trades[0].entry_time, trades[-1].entry_time
    edges = [t0 + (t1 - t0) * k / n_folds for k in range(n_folds + 1)]
    return [[t for t in trades if (edges[k] <= t.entry_time < edges[k + 1]) or (k == n_folds - 1 and t.entry_time == t1)]
            for k in range(n_folds)]
