"""Trade-level metrics. All R figures are net R = net P&L / (premium paid + entry fees), after the project's modelled
spread, fees and GST. Sharpe/Sortino are per-trade ratios, reported only with >= 50 trades."""
from __future__ import annotations

from collections import Counter

import numpy as np

from delta_intelligence.backtesting.option_backtest import max_drawdown_r, time_folds

MIN_RATIO_TRADES = 50
BAR_MIN = 5  # the backtester's bar


def max_consecutive_losses(r: np.ndarray) -> int:
    best = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


def trade_metrics(trades: list, window_bars: int | None = None) -> dict:
    """`window_bars` = number of 5m bars in the tested window (for exposure)."""
    if not trades:
        return {"n": 0}
    r = np.array([t.net_r for t in trades], float)
    pnl = np.array([t.net_pnl for t in trades], float)
    wins, losses = r[r > 0], r[r <= 0]
    gross_win, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    hold_bars = np.array([t.exit_idx - t.entry_idx for t in trades], float)
    out = {
        "n": len(r), "win_rate": float((r > 0).mean()), "expectancy_r": float(r.mean()), "median_r": float(np.median(r)),
        "avg_win_r": float(wins.mean()) if len(wins) else None, "avg_loss_r": float(losses.mean()) if len(losses) else None,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_r": max_drawdown_r(r), "max_consecutive_losses": max_consecutive_losses(r),
        "avg_hold_hours": float(hold_bars.mean() * BAR_MIN / 60.0),
        "exposure": float(hold_bars.sum() / window_bars) if window_bars else None,
        "fees_share_of_risk": float(np.mean([t.fees / t.max_loss for t in trades])),
        "underlying_r": float(np.mean([t.underlying_r for t in trades])),
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
        "sharpe_per_trade": None, "sortino_per_trade": None,
    }
    if len(r) >= MIN_RATIO_TRADES and r.std(ddof=1) > 0:
        out["sharpe_per_trade"] = float(r.mean() / r.std(ddof=1))
        down = np.minimum(r, 0)
        dd = np.sqrt((down ** 2).mean())
        out["sortino_per_trade"] = float(r.mean() / dd) if dd > 0 else None
    return out


def regime_breakdown(trades: list, regime_of) -> dict:
    """regime_of(trade) -> label. Returns label -> {n, expectancy_r, win_rate}."""
    groups: dict = {}
    for t in trades:
        groups.setdefault(regime_of(t), []).append(t.net_r)
    return {k: {"n": len(v), "expectancy_r": float(np.mean(v)), "win_rate": float(np.mean(np.array(v) > 0))}
            for k, v in sorted(groups.items())}


def fold_expectancies(trades: list, n_folds: int = 4) -> list:
    return [float(np.mean([t.net_r for t in f])) if f else None for f in time_folds(trades, n_folds)]


def straddle_economics(trades: list) -> dict:
    """Combined premium, maximum loss, break-even move for straddle trades (both legs are priced and charged fees).
    Premium per underlying unit is the sum of the two legs' fills; the strike is used as the spot proxy."""
    if not trades:
        return {}
    prem = np.array([sum(t.entry_fills) for t in trades], float)
    spot = np.array([float(np.mean(t.strikes)) for t in trades], float)
    fees = np.array([t.fees for t in trades], float)
    return {"avg_combined_premium_pct_of_spot": float(np.mean(prem / spot * 100)),
            "avg_max_loss_usd_per_unit": float(np.mean([t.max_loss for t in trades])),
            "avg_breakeven_move_pct": float(np.mean((prem + fees) / spot * 100)),
            "avg_fees_usd_per_unit": float(fees.mean())}
