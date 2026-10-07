"""Option-selection policies: HOW the same underlying signal is expressed as a bought option.

The underlying signal (S1-S10, legacy Supertrend) is untouched. A policy only decides expiry and strike (and may refuse
a contract) using information available at the signal timestamp. Every policy is a RESEARCH HYPOTHESIS, not a
recommendation: nothing here assumes any feature creates an edge. The ranking rule only orders contracts that already
passed the policy's filters, is deterministic, and is never tuned on the holdout.

`BASE` is the project's own, unmodified selection path (ATM/1-ITM, |delta| 0.40-0.60, nearest expiry with DTE >= 2.5x
the hold), run WITHOUT a picker, so BASE reproduces Phase 1 exactly.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.options.picking import Pick, PickContext
from delta_intelligence.options.pricing import greeks, year_fraction
from delta_intelligence.options.selector import SelectorConfig
from delta_intelligence.options.structures import long_call, long_put, long_straddle

RANKS = ("delta_mid", "low_spread", "low_theta")
STRIKE_WINDOW = 0.12  # candidate strikes must be within +-12% of spot


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class OptionPolicy:
    id: str
    delta_lo: float = 0.40  # |delta| band for directional options
    delta_hi: float = 0.60
    dte_lo: float | None = None  # days; None = the project's default expiry rule (nearest with DTE >= 2.5 x hold)
    dte_hi: float | None = None
    max_spread_pct: float | None = None  # (ask - bid) / mid, percent
    max_premium_pct_spot: float | None = None  # ask / spot, percent
    max_theta_pct_premium: float | None = None  # |model theta per day| / ask, percent
    max_iv_rv: float | None = None  # IV / realised vol of the underlying
    max_iv_percentile: float | None = None  # ATM IV percentile (0-100) at the signal bar
    min_open_interest: float | None = None  # REAL recorded data only
    min_volume: float | None = None  # REAL recorded data only
    rank: str = "delta_mid"
    default_path: bool = False  # True = no picker: the project's unmodified selection
    note: str = ""

    def __post_init__(self) -> None:
        if not 0 < self.delta_lo < self.delta_hi <= 1:
            raise PolicyError("need 0 < delta_lo < delta_hi <= 1")
        if (self.dte_lo is None) != (self.dte_hi is None):
            raise PolicyError("dte_lo and dte_hi go together")
        if self.dte_lo is not None and not 0 < self.dte_lo <= self.dte_hi:
            raise PolicyError("need 0 < dte_lo <= dte_hi")
        for n in ("max_spread_pct", "max_premium_pct_spot", "max_theta_pct_premium", "max_iv_rv", "min_open_interest",
                  "min_volume"):
            v = getattr(self, n)
            if v is not None and v <= 0:
                raise PolicyError(f"{n} must be > 0")
        if self.max_iv_percentile is not None and not 0 < self.max_iv_percentile <= 100:
            raise PolicyError("max_iv_percentile must be in (0, 100]")
        if self.rank not in RANKS:
            raise PolicyError(f"rank must be one of {RANKS}")

    @property
    def needs_real_liquidity(self) -> bool:
        return self.min_open_interest is not None or self.min_volume is not None


P = OptionPolicy
POLICIES: tuple[OptionPolicy, ...] = (
    P("BASE", default_path=True, note="the project's unmodified selection (ATM/1-ITM, |delta| 0.40-0.60, DTE >= 2.5x hold)"),
    P("DELTA-BROAD-030-060", 0.30, 0.60, note="any strike with |delta| 0.30-0.60, closest to 0.45"),
    P("DELTA-ITM-050-070", 0.50, 0.70, note="in-the-money: |delta| 0.50-0.70, closest to 0.60"),
    P("DELTA-OTM-020-040", 0.20, 0.40, note="out-of-the-money: |delta| 0.20-0.40, closest to 0.30"),
    P("DTE-2-3", dte_lo=2.0, dte_hi=3.0, note="ATM 0.40-0.60, nearest expiry 2-3 days out"),
    P("DTE-4-7", dte_lo=4.0, dte_hi=7.0, note="ATM 0.40-0.60, nearest expiry 4-7 days out"),
    P("DTE-8-14", dte_lo=8.0, dte_hi=14.0, note="ATM 0.40-0.60, nearest expiry 8-14 days out"),
    P("DTE-15-30", dte_lo=15.0, dte_hi=30.0, note="ATM 0.40-0.60, nearest expiry 15-30 days out"),
    P("FILTER-SPREAD-LE-6", max_spread_pct=6.0, rank="low_spread", note="skip contracts wider than 6% (ask-bid)/mid"),
    P("FILTER-THETA-LE-40", max_theta_pct_premium=40.0, rank="low_theta", note="skip contracts losing > 40% of premium/day"),
    P("FILTER-PREMIUM-LE-0.5", max_premium_pct_spot=0.5, note="skip contracts costing > 0.5% of spot"),
    P("FILTER-IVRV-LE-1.1", max_iv_rv=1.1, note="skip when IV > 1.1x realised vol (options rich versus movement)"),
    P("FILTER-IVPCT-LE-50", max_iv_percentile=50.0, note="skip when ATM IV is above its 50th percentile"),
    P("LIQ-OI100-VOL10", min_open_interest=100.0, min_volume=10.0,
      note="requires real open interest >= 100 and volume >= 10; cannot be evaluated on modelled data"),
)
BY_ID = {p.id: p for p in POLICIES}


def policy_dict(p: OptionPolicy) -> dict:
    return {k: getattr(p, k) for k in p.__dataclass_fields__}


# ---- the picker ----------------------------------------------------------------------------------------------------
def _expiry(ctx: PickContext, p: OptionPolicy, sel: SelectorConfig):
    for e in sorted(pd.Timestamp(x) for x in ctx.expiries):
        tte_d = (e - ctx.t_entry).total_seconds() / 86400.0
        tte_h = tte_d * 24.0
        if tte_h - sel.expiry_guard_hours < ctx.hold_hours:
            continue
        if p.dte_lo is None:
            if tte_d > sel.max_dte_days:
                return None
            if tte_h >= sel.dte_multiple * ctx.hold_hours:
                return e
        elif p.dte_lo <= tte_d <= p.dte_hi:
            return e
    return None


def _features(ctx: PickContext, kind: str, k: float, e: pd.Timestamp):
    f = ctx.fill(kind, k, e, True)
    if f is None:
        return None
    ask, mid, iv, src = f
    if not (ask > 0 and mid > 0):
        return None
    t = year_fraction(max(0.0, (e - ctx.t_entry).total_seconds()))
    th = float(greeks(ctx.spot, k, t, iv, kind)["theta_day"]) if iv and np.isfinite(iv) else None
    return {"ask": ask, "mid": mid, "iv": iv, "src": src, "spread_pct": 2 * (ask - mid) / mid * 100.0,
            "premium_pct_spot": ask / ctx.spot * 100.0, "theta_pct": None if th is None else abs(th) / ask * 100.0}


def _refuse(p: OptionPolicy, f: dict, ctx: PickContext, kind: str, k: float, e) -> str | None:
    """The name of the first filter this contract fails, or None."""
    if p.max_spread_pct is not None and f["spread_pct"] > p.max_spread_pct:
        return "filter_spread"
    if p.max_premium_pct_spot is not None and f["premium_pct_spot"] > p.max_premium_pct_spot:
        return "filter_premium"
    if p.max_theta_pct_premium is not None and (f["theta_pct"] is None or f["theta_pct"] > p.max_theta_pct_premium):
        return "filter_theta"
    if p.max_iv_rv is not None:
        rv = ctx.row.get("rv")
        if not rv or not f["iv"] or f["iv"] / rv > p.max_iv_rv:
            return "filter_iv_rv"
    if p.max_iv_percentile is not None:
        pc = ctx.row.get("iv_percentile")
        if pc is None or pc > p.max_iv_percentile:
            return "filter_iv_percentile"
    if p.needs_real_liquidity:
        liq = ctx.liquidity(kind, k, e)
        if liq is None:
            return "needs_real_liquidity_data"
        if p.min_open_interest is not None and (liq["open_interest"] or 0.0) < p.min_open_interest:
            return "filter_open_interest"
        if p.min_volume is not None and (liq["volume"] or 0.0) < p.min_volume:
            return "filter_volume"
    return None


def make_picker(p: OptionPolicy, sel: SelectorConfig | None = None):
    """A deterministic `contract_picker` for the backtester. BASE has none (the project's path is used as is)."""
    if p.default_path:
        return None
    sel = sel or SelectorConfig()
    mid_delta = (p.delta_lo + p.delta_hi) / 2.0

    def pick(ctx: PickContext) -> Pick:
        e = _expiry(ctx, p, sel)
        if e is None:
            return Pick(reason="no_expiry_for_policy")
        strikes = np.sort(np.asarray(ctx.strikes_for(e), float))
        strikes = strikes[np.abs(strikes / ctx.spot - 1.0) <= STRIKE_WINDOW]
        if not len(strikes):
            return Pick(reason="no_listed_options")
        exp = e.to_pydatetime()
        if ctx.policy != "LONG_OPTION":  # volatility view: ATM straddle; delta bands do not apply
            k = float(strikes[np.argmin(np.abs(strikes - ctx.spot))])
            fs = [_features(ctx, kind, k, e) for kind in ("C", "P")]
            if any(f is None for f in fs):
                return Pick(reason="no_price")
            for kind, f in zip("CP", fs):
                why = _refuse(p, f, ctx, kind, k, e)
                if why:
                    return Pick(reason=why)
            st = long_straddle(ctx.underlying, k, exp, ctx.contracts, ctx.contract_value,
                               (ctx.symbol_for("C", k, e), ctx.symbol_for("P", k, e)))
            return Pick(e, st, detail={"strike": k, "dte_days": (e - ctx.t_entry).total_seconds() / 86400.0})
        kind = "C" if ctx.direction == "LONG" else "P"
        survivors, fails = [], {}
        for k in strikes:
            d = ctx.abs_delta(e, kind, float(k))
            if d is None or not (p.delta_lo <= d <= p.delta_hi):
                continue
            f = _features(ctx, kind, float(k), e)
            if f is None:
                fails["no_price"] = fails.get("no_price", 0) + 1
                continue
            why = _refuse(p, f, ctx, kind, float(k), e)
            if why:
                fails[why] = fails.get(why, 0) + 1
                continue
            rank = {"delta_mid": abs(d - mid_delta), "low_spread": f["spread_pct"],
                    "low_theta": f["theta_pct"] if f["theta_pct"] is not None else 1e9}[p.rank]
            survivors.append((rank, f["spread_pct"], abs(float(k) - ctx.spot), float(k), d, f))
        if not survivors:
            reason = max(fails, key=fails.get) if fails else "no_strike_in_delta_band"
            return Pick(reason=reason)
        rank, _, _, k, d, f = min(survivors, key=lambda r: r[:4])  # deterministic: rank, spread, distance, strike
        build = long_call if kind == "C" else long_put
        st = build(ctx.underlying, k, exp, ctx.contracts, ctx.contract_value, ctx.symbol_for(kind, k, e))
        return Pick(e, st, detail={"strike": k, "abs_delta": d, "rank": p.rank, "rank_value": rank,
                                   "dte_days": (e - ctx.t_entry).total_seconds() / 86400.0,
                                   "spread_pct": f["spread_pct"], "n_eligible": len(survivors)})

    return pick
