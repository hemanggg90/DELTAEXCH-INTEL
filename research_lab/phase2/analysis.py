"""Phase 2 analysis: option metrics, cost attribution, signal-edge vs option-implementation, bucket tables, model-vs-real
quote comparison, discovery-only policy selection and the status layer (incl. REAL-DATA-VALIDATED).

Units: P&L in USD is per ONE underlying unit per trade (the backtester's unit size), so USD sums across assets are not
comparable. The R figures (net P&L / premium paid plus entry fees) are the comparable ones.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_backtest import max_drawdown_r
from lab import evaluate as E

REAL_DATA_VALIDATED = "REAL-DATA-VALIDATED"
OPTION_IMPLEMENTATION_FAILURE = "OPTION-IMPLEMENTATION FAILURE"


# ---- real-data thresholds (configurable; rationale in PROTOCOL2.md) ---------------------------------------------------
@dataclass(frozen=True)
class RealDataThresholds:
    """What 'predominantly real' means before REAL-DATA-VALIDATED may ever be used.

    Rationale (not arbitrary): the acceptance gates already need >= 200 trades and >= 30 per asset. Evaluating them on
    REAL trades only makes the modelled remainder irrelevant to the verdict, so the real-only trade counts must
    themselves meet those minimums. The fraction guards the other direction: if most of a strategy's signals could not be
    priced from real quotes, the real subset may be a biased sample (liquid hours/strikes only). 0.80 means at most one
    trade in five may be modelled; the real subset is then a near-complete census of the strategy's trades, and a
    modelled remainder of that size cannot move a pooled mean by more than a fifth of its own error. It is a
    configurable default, to be revisited with the measured model-vs-real error.
    """
    min_real_fraction: float = 0.80
    min_real_trades: int = E.MIN_TRADES
    min_real_per_asset: int = E.MIN_PER_ASSET
    min_snapshot_days: int = 14  # real data must span at least two weeks of snapshots to cover more than one regime


DEFAULT_REAL = RealDataThresholds()
MIN_DISCOVERY_TRADES = 100  # a policy needs this many discovery trades before it may be selected
MIN_POLICY_BREADTH = 0.5  # share of evaluable policies that must be positive on the holdout (robustness, not selection)


# ---- metrics -------------------------------------------------------------------------------------------------------
def _m(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.mean(xs)) if xs else None


def option_metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.net_r for t in trades], float)
    ml = np.array([t.max_loss for t in trades], float)
    pnl = np.array([t.net_pnl for t in trades], float)
    gross = np.array([t.gross_pnl if t.gross_pnl is not None else np.nan for t in trades], float)
    spread = np.array([t.spread_cost for t in trades], float)
    fees = np.array([t.fees for t in trades], float)
    theta = np.array([t.theta_cost_model if t.theta_cost_model is not None else np.nan for t in trades], float)
    wins, losses = r[r > 0], r[r <= 0]
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    hold_h = [(t.exit_time - t.entry_time).total_seconds() / 3600.0 for t in trades]
    ef = [t.entry_features for t in trades]
    real_both = [t.entry_price_source == "REAL_QUOTE" and t.exit_price_source == "REAL_QUOTE" for t in trades]
    return {
        "n": len(r), "gross_pnl_usd": float(np.nansum(gross)), "net_pnl_usd": float(pnl.sum()),
        "net_r": float(r.mean()), "win_rate": float((r > 0).mean()), "avg_win_r": float(wins.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.mean()) if len(losses) else None, "expectancy_r": float(r.mean()),
        "profit_factor": float(gw / gl) if gl > 0 else None, "max_drawdown_r": max_drawdown_r(r),
        "median_hold_hours": float(np.median(hold_h)),
        "avg_premium_pct_spot": _m(f.get("premium_pct_spot") for f in ef), "avg_spread_pct": _m(f.get("spread_pct") for f in ef),
        "avg_iv": _m(f.get("iv") for f in ef), "avg_rv": _m(f.get("rv") for f in ef), "avg_iv_rv": _m(f.get("iv_rv") for f in ef),
        "avg_abs_delta": _m(f.get("abs_delta") for f in ef), "avg_theta_pct_premium_per_day": _m(f.get("theta_pct_premium") for f in ef),
        "avg_theta_day_usd": _m(f.get("theta_day") for f in ef), "avg_gamma": _m(f.get("gamma") for f in ef),
        "avg_vega": _m(f.get("vega") for f in ef), "avg_dte_days": _m(f.get("dte_days") for f in ef),
        "fees_usd": float(fees.sum()), "spread_cost_usd": float(spread.sum()),
        "slippage_usd": None,  # not measurable: slippage needs real fills, and there are none (quotes only)
        "pct_real_entry": 100.0 * float(np.mean([t.entry_price_source == "REAL_QUOTE" for t in trades])),
        "pct_real_exit": 100.0 * float(np.mean([t.exit_price_source == "REAL_QUOTE" for t in trades])),
        "pct_real_both": 100.0 * float(np.mean(real_both)),
        "gross_r": _m(g / m for g, m in zip(gross, ml)), "spread_r": _m(s / m for s, m in zip(spread, ml)),
        "fees_r": _m(f / m for f, m in zip(fees, ml)), "theta_r": _m(th / m for th, m in zip(theta, ml)),
        "underlying_r": _m(t.underlying_r for t in trades),
    }


def attribution(trades: list) -> dict:
    """Where the underlying move went. In R (comparable across assets): net = gross - spread - fees, exactly; theta is
    an ESTIMATE (model theta x time held) shown inside gross; `move_and_other` is the rest of gross."""
    m = option_metrics(trades)
    if not m["n"]:
        return {}
    gross_r, spread_r, fees_r, theta_r = m["gross_r"], m["spread_r"], m["fees_r"], m["theta_r"]
    return {"n": m["n"], "net_r": m["net_r"], "gross_r": gross_r, "spread_r": -spread_r, "fees_r": -fees_r,
            "theta_r_estimate": None if theta_r is None else -theta_r,
            "move_and_other_r_estimate": None if theta_r is None else gross_r + theta_r,
            "identity_gap_r": m["net_r"] - (gross_r - spread_r - fees_r)}


def bootstrap_ci_low(x, q: float = 5.0, n: int = 2000, seed: int = 11) -> float | None:
    x = np.asarray([v for v in x if v is not None and np.isfinite(v)], float)
    if len(x) < 10:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(means, q))


def split(trades: list):
    return E.split_holdout(sorted(trades, key=lambda t: t.entry_time))


def signal_vs_option(trades: list, min_trades: int = 30) -> dict:
    """Does the UNDERLYING signal have an edge, and does the option implementation preserve it?

    Signal edge = mean underlying-space R (stop/target geometry, no option, no costs; for straddles, which have no
    direction, the frictionless mid-to-mid option return). Shown only if positive in BOTH discovery and holdout and its
    bootstrap 95% one-sided lower bound is above zero. Implementation edge = net R after spread, fees, theta."""
    if not trades:
        return {"classification": "DATA-INSUFFICIENT", "reason": "no trades"}
    disc, hold = split(trades)
    vol = all(t.structure.startswith("LONG_S") for t in trades)

    def sig(ts):
        return [(t.gross_pnl / t.max_loss) if vol and t.gross_pnl is not None else t.underlying_r for t in ts]

    sd, sh = sig(disc), sig(hold)
    out = {"signal_metric": "frictionless option R (volatility view)" if vol else "underlying R (stop/target geometry)",
           "signal_r_discovery": _m(sd), "signal_r_holdout": _m(sh), "n_discovery": len(disc), "n_holdout": len(hold),
           "signal_ci_low_pooled": bootstrap_ci_low(sd + sh),
           "gross_r_holdout": _m(t.gross_pnl / t.max_loss for t in hold if t.gross_pnl is not None),
           "net_r_discovery": _m(t.net_r for t in disc), "net_r_holdout": _m(t.net_r for t in hold)}
    if len(disc) < min_trades or len(hold) < min_trades:
        out.update(classification="DATA-INSUFFICIENT", reason=f"{len(disc)} discovery / {len(hold)} holdout trades "
                                                              f"(need {min_trades} each)")
        return out
    sig_pos = (out["signal_r_discovery"] or 0) > 0 and (out["signal_r_holdout"] or 0) > 0 and \
        (out["signal_ci_low_pooled"] is not None and out["signal_ci_low_pooled"] > 0)
    net_pos = (out["net_r_holdout"] or 0) > 0
    if not sig_pos:
        out["classification"] = ("NET POSITIVE WITHOUT A SHOWN SIGNAL EDGE (treat as noise)" if net_pos
                                 else "NO SIGNAL EDGE (strategy failure)")
    elif net_pos:
        out["classification"] = "SIGNAL EDGE PRESERVED BY THE OPTION IMPLEMENTATION"
    else:
        why = "costs: gross is positive, spread/fees consume it" if (out["gross_r_holdout"] or 0) > 0 \
            else "decay/convexity: gross is not positive even before costs"
        out["classification"] = f"{OPTION_IMPLEMENTATION_FAILURE} ({why})"
    return out


# ---- bucket tables --------------------------------------------------------------------------------------------------
def by_bucket(trades: list, feature: str, edges: list, labels: list | None = None) -> pd.DataFrame:
    rows = []
    for t in trades:
        v = t.entry_features.get(feature)
        if v is None or not np.isfinite(v):
            continue
        rows.append({"v": v, "net_r": t.net_r, "gross_r": (t.gross_pnl / t.max_loss) if t.gross_pnl is not None else np.nan,
                     "spread_r": t.spread_cost / t.max_loss, "fees_r": t.fees / t.max_loss, "win": t.net_r > 0,
                     "spread_pct": t.entry_features.get("spread_pct"), "real": t.entry_price_source == "REAL_QUOTE"})
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["bucket"] = pd.cut(d["v"], edges, labels=labels, include_lowest=True)
    g = d.groupby("bucket", observed=True).agg(n=("net_r", "size"), net_r=("net_r", "mean"), gross_r=("gross_r", "mean"),
                                               spread_r=("spread_r", "mean"), fees_r=("fees_r", "mean"),
                                               win_rate=("win", "mean"), avg_spread_pct=("spread_pct", "mean"),
                                               pct_real=("real", "mean"))
    g["pct_real"] = 100 * g["pct_real"]
    return g.reset_index()


BUCKETS = {
    "dte_days": ([0, 1, 3, 7, 14, 30, 100], ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", ">30d"]),
    "abs_delta": ([0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.01], ["<0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5", "0.5-0.6", "0.6-0.7", ">0.7"]),
    "spread_pct": ([0, 3, 6, 10, 20, 1000], ["<3%", "3-6%", "6-10%", "10-20%", ">20%"]),
    "iv_rv": ([0, 0.8, 1.0, 1.2, 1.5, 100], ["<0.8", "0.8-1.0", "1.0-1.2", "1.2-1.5", ">1.5"]),
    "premium_pct_spot": ([0, 0.25, 0.5, 1.0, 2.0, 100], ["<0.25%", "0.25-0.5%", "0.5-1%", "1-2%", ">2%"]),
    "theta_pct_premium": ([0, 20, 40, 60, 100, 10000], ["<20%", "20-40%", "40-60%", "60-100%", ">100%"]),
    "strike_dist_pct": ([0, 0.5, 1, 2, 4, 100], ["<0.5%", "0.5-1%", "1-2%", "2-4%", ">4%"]),
}


def bucket_tables(trades: list) -> dict:
    return {f: by_bucket(trades, f, e, l) for f, (e, l) in BUCKETS.items()}


# ---- model vs real recorded quotes -------------------------------------------------------------------------------------
def model_vs_real(assessed: pd.DataFrame, market, max_rows: int = 20000, seed: int = 5) -> dict:
    """For each real two-sided recorded quote, what would the project's model have said at that moment (same contract,
    same time, as-of IV)? Compares mid, ask, bid and half-spread. Labelled MODEL-vs-REAL validation, not a backtest."""
    from delta_intelligence.options.pricing import bs_price, year_fraction

    d = assessed[(assessed["tier"] == "REAL_QUOTE") & (assessed["underlying"] == market.underlying)].copy()
    d = d[(d["dte_days"] >= 0.25) & ((d["strike"] / d["spot"] - 1).abs() <= 0.08)]
    if len(d) > max_rows:
        d = d.sample(max_rows, random_state=seed)
    rows = []
    for r in d.itertuples():
        t_h = (r.expiry - r.taken_at).total_seconds() / 3600.0
        iv, _ = market.leg_iv_with_source(r.taken_at, t_h, float(r.strike), float(r.spot), allow_neighbor=True)
        if iv is None:
            continue
        mid = float(bs_price(r.spot, r.strike, year_fraction(t_h * 3600), iv, r.kind))
        h = max(market.half_spread(float(r.strike), float(r.spot)) * mid, market.tick / 2)
        ask = np.ceil((mid + h) / market.tick - 1e-9) * market.tick
        bid = max(np.floor((mid - h) / market.tick + 1e-9) * market.tick, 0.0)
        rows.append({"dte": r.dte_days, "k": abs(r.strike / r.spot - 1) * 100, "real_mid": (r.bid + r.ask) / 2,
                     "real_ask": r.ask, "real_bid": r.bid, "m_mid": mid, "m_ask": ask, "m_bid": bid,
                     "real_half": (r.ask - r.bid) / 2 / ((r.bid + r.ask) / 2) * 100, "m_half": (ask - bid) / 2 / mid * 100 if mid > 0 else np.nan})
    if not rows:
        return {"n": 0, "n_candidates": int(len(d))}
    x = pd.DataFrame(rows)
    x["mid_err_pct"] = (x["m_mid"] / x["real_mid"] - 1) * 100
    x["ask_err_pct"] = (x["m_ask"] / x["real_ask"] - 1) * 100
    x["bid_err_pct"] = (x["m_bid"] / x["real_bid"] - 1) * 100
    x["dte_b"] = pd.cut(x["dte"], [0, 1, 3, 7, 14, 100], labels=["<1d", "1-3d", "3-7d", "7-14d", ">14d"])
    x["k_b"] = pd.cut(x["k"], [-0.01, 1, 3, 8], labels=["ATM<1%", "1-3%", "3-8%"])

    def summ(g):
        return pd.Series({"n": len(g), "median_mid_err_pct": g["mid_err_pct"].median(),
                          "median_abs_mid_err_pct": g["mid_err_pct"].abs().median(),
                          "median_ask_err_pct": g["ask_err_pct"].median(), "median_bid_err_pct": g["bid_err_pct"].median(),
                          "real_half_spread_pct": g["real_half"].median(), "model_half_spread_pct": g["m_half"].median()})

    return {"n": len(x), "n_candidates": int(len(d)), "overall": summ(x).to_dict(),
            "by_dte": x.groupby("dte_b", observed=True).apply(summ, include_groups=False).reset_index(),
            "by_moneyness": x.groupby("k_b", observed=True).apply(summ, include_groups=False).reset_index()}


# ---- discovery-only selection and status ------------------------------------------------------------------------------
def select_from_sums(table: dict, order: list[str], min_disc: int = MIN_DISCOVERY_TRADES) -> dict:
    """Choose ONE policy per cell using DISCOVERY numbers only. `table[pid] = (n_discovery, mean_net_r_discovery)`.
    The highest discovery mean net R among policies with at least `min_disc` discovery trades; ties go to the earlier
    policy in the library (BASE first); if none qualifies the choice is BASE. The holdout is never read here."""
    shown = {pid: {"n_discovery": table.get(pid, (0, None))[0], "discovery_net_r": table.get(pid, (0, None))[1]} for pid in order}
    elig = [(pid, v["discovery_net_r"]) for pid, v in shown.items() if v["n_discovery"] >= min_disc and v["discovery_net_r"] is not None]
    if not elig:
        return {"selected": "BASE", "reason": f"no policy had >= {min_disc} discovery trades; BASE kept", "table": shown}
    best = max(elig, key=lambda x: (x[1], -order.index(x[0])))
    return {"selected": best[0], "reason": f"highest discovery net R {best[1]:+.3f} among {len(elig)} eligible policies", "table": shown}


def select_policy(per_policy: dict, order: list[str], min_disc: int = MIN_DISCOVERY_TRADES) -> dict:
    """Same selection from trade lists (used by tests and small runs)."""
    table = {}
    for pid in order:
        disc, _ = split(per_policy.get(pid, []))
        table[pid] = (len(disc), _m(t.net_r for t in disc))
    return select_from_sums(table, order, min_disc)


def breadth_from_sums(hold: dict, evaluable: list[str]) -> tuple[float | None, int]:
    """`hold[pid] = (n_holdout, mean_net_r_holdout)`. Share of evaluable policies (>= 30 holdout trades) that are positive."""
    pos = tot = 0
    for pid in evaluable:
        n, m = hold.get(pid, (0, None))
        if n >= E.MIN_HOLDOUT and m is not None:
            tot += 1
            pos += m > 0
    return (pos / tot if tot else None), tot


def policy_breadth(per_policy: dict, evaluable: list[str]) -> tuple[float | None, int]:
    """A robustness check on the signal's economics across option choices; it is NOT used to select anything."""
    hold = {}
    for pid in evaluable:
        _, h = split(per_policy.get(pid, []))
        hold[pid] = (len(h), _m(t.net_r for t in h))
    return breadth_from_sums(hold, evaluable)


# ---- compact, poolable summaries (so workers can spill trades to disk and return only these) ---------------------------
POOL_MEAN_FIELDS = ("net_r", "win_rate", "gross_r", "spread_r", "fees_r", "theta_r", "underlying_r", "avg_premium_pct_spot",
                    "avg_spread_pct", "avg_iv", "avg_rv", "avg_iv_rv", "avg_abs_delta", "avg_theta_pct_premium_per_day",
                    "avg_theta_day_usd", "avg_gamma", "avg_vega", "avg_dte_days", "median_hold_hours", "pct_real_entry",
                    "pct_real_exit", "pct_real_both")
POOL_SUM_FIELDS = ("gross_pnl_usd", "net_pnl_usd", "fees_usd", "spread_cost_usd")


def period_metrics(trades: list) -> dict:
    d, h = split(trades)
    return {"all": option_metrics(trades), "discovery": option_metrics(d), "holdout": option_metrics(h)}


def pool_metrics(ms: list[dict]) -> dict:
    """Pool per-asset `option_metrics` dicts: counts and USD sums add, means are n-weighted. Profit factor, drawdown and
    average win/loss are sequence- or sign-dependent and are NOT pooled (None): look at the per-asset values for those."""
    ms = [m for m in ms if m.get("n")]
    if not ms:
        return {"n": 0}
    n = sum(m["n"] for m in ms)
    out = {"n": n, "pooled_from_assets": len(ms), "profit_factor": None, "max_drawdown_r": None, "avg_win_r": None, "avg_loss_r": None,
           "slippage_usd": None}
    for k in POOL_MEAN_FIELDS:
        vs = [(m["n"], m[k]) for m in ms if m.get(k) is not None]
        out[k] = sum(a * b for a, b in vs) / sum(a for a, _ in vs) if vs else None
    for k in POOL_SUM_FIELDS:
        out[k] = sum(m.get(k, 0.0) or 0.0 for m in ms)
    out["expectancy_r"] = out["net_r"]
    return out


BUCKET_FIELDS = ("n", "net", "gross", "spread", "fees", "wins", "sp_sum", "sp_n", "real")


def bucket_sums(trades: list) -> dict:
    """Additive bucket statistics per feature: {feature: {label: [n, net_r, gross_r, spread_r, fees_r, wins, spread_pct_sum,
    spread_pct_n, real_entry]}}. Summing these across assets/policies equals computing the table on the pooled trades."""
    out: dict = {}
    for feat, (edges, labels) in BUCKETS.items():
        vals = {}
        for t in trades:
            v = t.entry_features.get(feat)
            if v is None or not np.isfinite(v):
                continue
            idx = int(np.searchsorted(edges, v, side="left")) - 1 if v > edges[0] else 0
            idx = min(max(idx, 0), len(labels) - 1)
            row = vals.setdefault(labels[idx], [0.0] * len(BUCKET_FIELDS))
            sp = t.entry_features.get("spread_pct")
            g = (t.gross_pnl / t.max_loss) if t.gross_pnl is not None else 0.0
            for i, x in enumerate((1, t.net_r, g, t.spread_cost / t.max_loss, t.fees / t.max_loss, float(t.net_r > 0),
                                   sp if sp is not None and np.isfinite(sp) else 0.0, 1.0 if sp is not None and np.isfinite(sp) else 0.0,
                                   float(t.entry_price_source == "REAL_QUOTE"))):
                row[i] += x
        out[feat] = vals
    return out


def merge_bucket_sums(parts: list[dict]) -> dict:
    out: dict = {}
    for p in parts:
        for feat, labs in p.items():
            for lab, row in labs.items():
                tgt = out.setdefault(feat, {}).setdefault(lab, [0.0] * len(BUCKET_FIELDS))
                for i, x in enumerate(row):
                    tgt[i] += x
    return out


def bucket_frames(sums: dict) -> dict:
    frames = {}
    for feat, (edges, labels) in BUCKETS.items():
        rows = []
        for lab in labels:
            r = sums.get(feat, {}).get(lab)
            if r and r[0]:
                n = r[0]
                rows.append({"bucket": lab, "n": int(n), "net_r": r[1] / n, "gross_r": r[2] / n, "spread_r": r[3] / n, "fees_r": r[4] / n,
                             "win_rate": r[5] / n, "avg_spread_pct": r[6] / r[7] if r[7] else None, "pct_real": 100 * r[8] / n})
        frames[feat] = pd.DataFrame(rows)
    return frames


def real_fraction(trades: list) -> float:
    return float(np.mean([t.entry_price_source == "REAL_QUOTE" and t.exit_price_source == "REAL_QUOTE" for t in trades])) if trades else 0.0


def final_status(cell: dict, n_cells: int, breadth: float | None, real_cell: dict | None = None,
                 real: RealDataThresholds = DEFAULT_REAL, snapshot_days: int = 0) -> dict:
    """Existing acceptance gates + policy-breadth gate (+ REAL-DATA-VALIDATED when the real-data bar is met).

    `cell` holds the SELECTED policy's results in the same shape `lab.evaluate.classify` expects. `real_cell` (optional)
    holds the REAL_ONLY results of the same configuration."""
    v = E.classify(cell, n_cells)
    out = {"status": v["status"], "reasons": list(v["reasons"]), "gates": dict(v["gates"]), "real_data": {}}
    if v["status"] == E.ACCEPTED and breadth is not None and breadth < MIN_POLICY_BREADTH:
        out["status"] = E.EXPERIMENTAL
        out["reasons"].append(f"policy_breadth: only {breadth:.0%} of evaluable option policies are positive on the "
                              f"holdout (need >= {MIN_POLICY_BREADTH:.0%})")
    out["gates"]["policy_breadth"] = {"ok": None if breadth is None else breadth >= MIN_POLICY_BREADTH,
                                      "detail": "n/a" if breadth is None else f"{breadth:.0%} of evaluable policies positive"}
    rd = out["real_data"]
    if real_cell is None:
        rd.update(checked=False, detail="no REAL_ONLY results (no recorded option data overlaps the signals)")
        return out
    real_trades = E.pooled(real_cell["assets"])
    rd["real_trades"] = len(real_trades)
    rd["snapshot_days"] = snapshot_days
    per_asset = {a: len(r["trades"]) for a, r in real_cell["assets"].items()}
    rd["per_asset_real_trades"] = per_asset
    frac = real_fraction(E.pooled(cell["assets"]))
    rd["real_fraction"] = frac
    problems = []
    if len(real_trades) < real.min_real_trades:
        problems.append(f"{len(real_trades)} real trades (need {real.min_real_trades})")
    if sum(1 for n in per_asset.values() if n >= real.min_real_per_asset) < E.MIN_ASSETS:
        problems.append(f"fewer than {E.MIN_ASSETS} assets with >= {real.min_real_per_asset} real trades")
    if snapshot_days < real.min_snapshot_days:
        problems.append(f"{snapshot_days} days of recorded snapshots (need {real.min_snapshot_days})")
    if frac < real.min_real_fraction:
        problems.append(f"{frac:.0%} of trades priced from real quotes at both ends (need {real.min_real_fraction:.0%})")
    if problems:
        rd.update(checked=True, validated=False, detail="; ".join(problems))
        return out
    rv = E.classify(real_cell, n_cells)
    rd.update(checked=True, real_only_status=rv["status"], real_only_reasons=rv["reasons"])
    if out["status"] == E.ACCEPTED and rv["status"] == E.ACCEPTED:
        out["status"] = REAL_DATA_VALIDATED
        rd.update(validated=True, detail="all gates pass on modelled+real AND on real-only trades; real-data bar met")
    else:
        rd.update(validated=False, detail="gates on real-only trades: " + rv["status"])
    return out
