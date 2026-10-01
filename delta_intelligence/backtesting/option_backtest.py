"""
Hybrid option backtest: signals on the underlying's perp, P&L as a defined-risk option structure.

For each setup (one trade at a time per strategy, no overlap):

1. **Entry**, at the signal bar's CLOSE:
   - expiry = nearest daily with >= `min_hours_to_expiry` remaining;
   - legs = selector policy on that expiry's listed strikes;
   - each leg filled at the modelled ask (buys) or bid (sells), at the AS-OF implied vol.
2. **Exit**, the first of:
   - the underlying stop (checked BEFORE the target within a bar: conservative);
   - the underlying target;
   - `max_hold_bars`;
   - the close-before-settlement guard (user rule: 30 min before 17:30 IST).

   Stop/target exits are valued at the trigger level converted to index terms, at that bar's close time; other
   exits at the index close.
3. **Costs:** Delta's option fee per leg per fill (0.01% of notional, capped at 3.5% of premium) + GST, plus the
   spread already paid in the fills.
4. **R:** net USD P&L / (the structure's max loss at the entry fills + entry fees). This is what the risk engine
   sizes on, so ranking happens in the same unit.
5. **Validation:** wherever the chosen contract actually traded on the entry or exit bar, the model mid is compared
   with the real trade price. That is the report's model-vs-reality check.

Trades without a modelled price (an IV coverage gap, missing index, or unlisted strikes) are SKIPPED and counted by
reason. Prices are never invented.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.options.selector import build_structure, choose_expiry
from delta_intelligence.options.structures import leg_fee
from delta_intelligence.strategies.base import Setup, Strategy

BAR = pd.Timedelta(minutes=5)


@dataclass
class OptionBacktestConfig:
    policy: str | None = None  # None = the strategy's default_structure
    min_hours_to_expiry: float = 6.0
    close_before_settlement_min: int = 30
    max_hold_bars: int = 48  # 4 hours of 5m bars
    contract_value: float = 0.001
    units: float = 1.0  # position size in underlying units (R is size-invariant up to fee rounding)
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
    entry_time: pd.Timestamp  # decision time = signal bar close
    exit_time: pd.Timestamp
    exit_reason: str
    expiry: pd.Timestamp
    strikes: tuple
    entry_fills: list
    exit_fills: list
    entry_iv: float
    fees: float
    max_loss: float  # USD, incl. entry fees (R denominator)
    net_pnl: float
    net_r: float
    underlying_r: float  # gross R of the same trade on the perp (stop distance = 1R)
    validation: list = field(default_factory=list)  # (when, symbol, model_mid, real_price)

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
            "expiry", "entry_iv", "fees", "max_loss", "net_pnl", "net_r", "underlying_r")} for t in self.trades])


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
                self._by_expiry[expiry] = {(s, pd.Timestamp(t)): float(c)
                                           for s, t, c in zip(df["symbol"], pd.to_datetime(df["timestamp"], utc=True),
                                                              df["close"])}
        return self._by_expiry[expiry].get((symbol, pd.Timestamp(bar_open)))


def _contracts(cfg: OptionBacktestConfig) -> int:
    return max(1, int(round(cfg.units / cfg.contract_value)))


def run_option_backtest(strategy: Strategy, frame: pd.DataFrame, market: OptionMarketModel,
                        cfg: OptionBacktestConfig | None = None, real: RealOptionPrices | None = None,
                        setups: list[Setup] | None = None) -> OptionBacktestResult:
    cfg = cfg or OptionBacktestConfig()
    policy = cfg.policy or strategy.default_structure
    setups = setups if setups is not None else strategy.historical_setups(frame)
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    ts_list = list(ts)  # plain lists: per-bar pandas .iloc is too slow over ~90k bars x 14 strategies
    close_t = list(ts + BAR)
    pos = {t: i for i, t in enumerate(ts_list)}
    hi, lo, close = frame["high"].to_numpy(), frame["low"].to_numpy(), frame["close"].to_numpy()
    idx_close = frame["index_close"].to_numpy() if "index_close" in frame else np.full(len(frame), np.nan)
    listed = set(market.listed_strikes)
    contracts = _contracts(cfg)
    skipped: Counter = Counter()
    trades: list[OptionTrade] = []
    next_free = 0

    for s in setups:
        i = pos.get(pd.Timestamp(s.timestamp))
        if i is None or i < next_free:
            skipped["overlap"] += 1 if i is not None else 0
            continue
        spot = idx_close[i]
        if not np.isfinite(spot):
            skipped["no_index"] += 1
            continue
        t_entry = close_t[i]
        expiry = choose_expiry(t_entry, cfg.min_hours_to_expiry, listed or None)
        if expiry is None:
            skipped["no_listed_expiry"] += 1
            continue
        basis = spot / close[i]
        try:
            st = build_structure(policy, s.direction, market.underlying, spot, expiry,
                                 market.listed_strikes.get(expiry, np.array([])), s.stop_price * basis,
                                 s.target_price * basis, contracts, cfg.contract_value,
                                 symbol_for=lambda k, x, e: market.symbols.get((k, x, e), ""))
        except ValueError:
            skipped["strikes"] += 1
            continue
        entry = [market.fill(leg.kind, leg.strike, expiry, t_entry, spot, buy=leg.side > 0) for leg in st.legs]
        if any(e is None for e in entry):
            skipped["no_iv"] += 1
            continue
        entry_px = [e[0] for e in entry]
        units = [st.units(leg) for leg in st.legs]
        fee_in = sum(leg_fee(p, spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                     for p, u in zip(entry_px, units))
        max_loss = st.max_loss(entry_px) + fee_in
        if max_loss <= 0:
            skipped["degenerate"] += 1
            continue

        # ---- walk forward ------------------------------------------------------------------------------------
        guard = expiry - pd.Timedelta(minutes=cfg.close_before_settlement_min)
        j_exit, reason, level = None, None, None
        last = min(len(frame) - 1, i + cfg.max_hold_bars)
        for j in range(i + 1, last + 1):
            bar_close = close_t[j]
            if s.direction == "LONG":
                hit_stop, hit_tgt = lo[j] <= s.stop_price, hi[j] >= s.target_price
            else:
                hit_stop, hit_tgt = hi[j] >= s.stop_price, lo[j] <= s.target_price
            if hit_stop:
                j_exit, reason, level = j, "STOP", s.stop_price
            elif hit_tgt:
                j_exit, reason, level = j, "TARGET", s.target_price
            elif bar_close >= guard:
                j_exit, reason = j, "SETTLEMENT_GUARD"
            elif j == last:
                j_exit, reason = j, "TIME" if j == i + cfg.max_hold_bars else "END_OF_DATA"
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
        ex = [market.fill(leg.kind, leg.strike, expiry, t_exit, exit_spot, buy=leg.side < 0) for leg in st.legs]
        if any(e is None for e in ex):
            skipped["no_iv_exit"] += 1
            continue
        exit_px = [e[0] for e in ex]
        fee_out = sum(leg_fee(p, exit_spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                      for p, u in zip(exit_px, units))
        gross = sum(leg.side * (xp - ep) * u for leg, ep, xp, u in zip(st.legs, entry_px, exit_px, units))
        net = gross - fee_in - fee_out
        under_exit = level if level is not None else close[j_exit]
        sign = 1 if s.direction == "LONG" else -1
        under_r = sign * (under_exit - s.entry_price) / s.risk if s.risk > 0 else 0.0

        validation = []
        if real is not None:
            for when, bar_i, fills in (("entry", i, entry), ("exit", j_exit, ex)):
                for leg, (_, mid, _) in zip(st.legs, fills):
                    p = real.price(leg.symbol, expiry, ts_list[bar_i]) if leg.symbol else None
                    if p is not None:
                        validation.append((when, leg.symbol, mid, p))

        trades.append(OptionTrade(
            strategy=strategy.name, structure=st.name, direction=s.direction, entry_idx=i, exit_idx=j_exit,
            entry_time=t_entry, exit_time=t_exit, exit_reason=reason, expiry=expiry,
            strikes=tuple(leg.strike for leg in st.legs), entry_fills=entry_px, exit_fills=exit_px,
            entry_iv=float(entry[0][2]), fees=fee_in + fee_out, max_loss=max_loss, net_pnl=net,
            net_r=net / max_loss, underlying_r=under_r, validation=validation))
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
    """Chronological folds over the trade period. Parameters are fixed in advance and never re-fitted per fold,
    so each fold is an honest out-of-sample window."""
    if not trades:
        return []
    t0, t1 = trades[0].entry_time, trades[-1].entry_time
    edges = [t0 + (t1 - t0) * k / n_folds for k in range(n_folds + 1)]
    return [[t for t in trades if (edges[k] <= t.entry_time < edges[k + 1]) or (k == n_folds - 1 and t.entry_time == t1)]
            for k in range(n_folds)]
