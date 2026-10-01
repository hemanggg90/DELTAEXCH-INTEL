"""
Strategy search: pick the 10 best strategy variants by WIN RATE and DRAWDOWN among those with POSITIVE net R, without
fooling ourselves.

Selecting the best of hundreds of variants on the same data always finds winners, some by pure luck. So:
1. **DISCOVERY** (2025-12-01 → 2026-06-30, BTC + ETH):
   - every candidate is back-tested as bought options;
   - eligible = positive net R on BTC AND on ETH (>= 40 trades each);
   - ranked by pooled win rate (desc), then max drawdown in R (smaller first);
   - at most 2 variants per strategy family;
   - top 10 selected.
2. **HOLDOUT** (2026-07-01 → 2026-10-01, BTC + ETH, plus XAUT whose options start 2026-07-24): never used for
   selection. Each pick's holdout result is reported as is.
3. **NULL BENCHMARK:** every candidate is re-run with RANDOM directions at the same entry times. If random signals
   produce "winners" as good as ours in discovery, the discovery ranking carries no information.

    python scripts/strategy_search.py                 # writes docs/research/STRATEGY_SEARCH.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_END = pd.Timestamp("2026-07-01T00:00Z")
SINCE = dt.datetime(2025, 12, 1, tzinfo=dt.timezone.utc)
MIN_TRADES_EACH = 40
PER_FAMILY_CAP = 2


def candidates():
    """(candidate_id, family, strategy class, params, moneyness, hold_mult)."""
    from delta_intelligence.strategies import legacy_v2_library as v2
    from delta_intelligence.strategies import library_v3 as v3

    classes = [c for c in v3.V3_STRATEGIES if c is not v3.PreEventStraddle] + list(v2.ALL_STRATEGIES)
    out = []
    for cls in classes:
        base = dict(cls.default_parameters)
        exit_variants = []
        if "rr" in base:
            for rr in (1.5, 2.0, 3.0):
                exit_variants.append((f"rr{rr}", {**base, "rr": rr}))
        else:
            for tag, sm, tm in (("tight", 0.75, 0.75), ("base", 1.0, 1.0), ("wide", 1.33, 1.5)):
                p = dict(base)
                if "stop_atr_mult" in p:
                    p["stop_atr_mult"] = round(p["stop_atr_mult"] * sm, 3)
                if "target_atr_mult" in p:
                    p["target_atr_mult"] = round(p["target_atr_mult"] * tm, 3)
                exit_variants.append((tag, p))
        for tag, params in exit_variants:
            for money in ("ATM", "ITM1"):
                for hold in (1, 2):
                    cid = f"{cls.name} | {tag} | {money} | hold x{hold}"
                    out.append((cid, cls.name, cls, params, money, hold))
    return out


def make_strategy(cls, params, hold_mult):
    s = cls(params)
    if hold_mult != 1:
        s.max_hold_bars = int(s.max_hold_bars * hold_mult)
        s.expected_hold_bars = int(s.expected_hold_bars * hold_mult)
    return s


def randomize(setups, seed):
    """Same entry times, random LONG/SHORT; stop and target mirrored around the entry when flipped."""
    from delta_intelligence.strategies.base import Setup

    rng = np.random.default_rng(seed)
    out = []
    for s in setups:
        if rng.random() < 0.5:
            out.append(s)
        else:
            d = "SHORT" if s.direction == "LONG" else "LONG"
            out.append(Setup(s.timestamp, d, s.entry_price, 2 * s.entry_price - s.stop_price,
                             2 * s.entry_price - s.target_price, s.strategy, s.meta))
    return out


def run_underlying(perp: str) -> pd.DataFrame:
    sys.path.insert(0, str(ROOT / "scripts"))
    from research_report_v3 import load_market

    from delta_intelligence.backtesting.option_backtest import OptionBacktestConfig, run_option_backtest
    from delta_intelligence.config.settings import get_settings
    from delta_intelligence.config.watchlist import underlying_for
    from delta_intelligence.data.data_manager import DataManager
    from delta_intelligence.features.feature_engine import compute_features
    from delta_intelligence.features.inputs import load_feature_inputs
    from delta_intelligence.options.iv_history import IvHistoryStore
    from delta_intelligence.options.selector import SelectorConfig
    from delta_intelligence.strategies.context import build_strategy_frame

    t0 = time.time()
    s = get_settings()
    u = underlying_for(perp)
    dm = DataManager.from_settings(s)
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    inp = load_feature_inputs(dm, perp, "5m", SINCE - dt.timedelta(days=3))
    market, _, hourly = load_market(store, u.asset, inp.aux.index)
    frame = build_strategy_frame(inp.ohlcv, compute_features(inp.ohlcv, "5m", inp.aux), hourly)
    frame = frame[frame["timestamp"] >= pd.Timestamp(SINCE)].reset_index(drop=True)
    rows = []
    cands = candidates()
    for n, (cid, fam, cls, params, money, hold) in enumerate(cands):
        strat = make_strategy(cls, params, hold)
        setups = strat.historical_setups(frame)
        cfg = OptionBacktestConfig(selector=SelectorConfig(moneyness=money))
        for kind, sset in (("real", setups), ("random", randomize(setups, seed=n))):
            res = run_option_backtest(strat, frame, market, cfg, setups=sset)
            for t in res.trades:
                rows.append((cid, fam, kind, u.asset, t.entry_time, t.net_r, t.premium_source))
        if n % 20 == 0:
            print(f"[{u.asset}] {n}/{len(cands)} candidates ({time.time() - t0:.0f}s)", flush=True)
    return pd.DataFrame(rows, columns=["cid", "family", "kind", "underlying", "entry_time", "net_r", "source"])


def max_dd(r: np.ndarray) -> float:
    if not len(r):
        return 0.0
    eq = np.cumsum(r)
    return float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())


def stats(df: pd.DataFrame) -> dict:
    if not len(df):
        return {"n": 0, "net_r": None, "win": None, "dd": None}
    r = df.sort_values("entry_time")["net_r"].to_numpy()
    return {"n": len(r), "net_r": float(r.mean()), "win": float((r > 0).mean()), "dd": max_dd(r)}


def select(trades: pd.DataFrame, kind: str) -> pd.DataFrame:
    d = trades[(trades["kind"] == kind) & (trades["entry_time"] < DISCOVERY_END)
               & trades["underlying"].isin(["BTC", "ETH"])]
    rows = []
    for (cid, fam), g in d.groupby(["cid", "family"]):
        per = {u: stats(g[g["underlying"] == u]) for u in ("BTC", "ETH")}
        pooled = stats(g)
        ok = all(per[u]["n"] >= MIN_TRADES_EACH and (per[u]["net_r"] or 0) > 0 for u in per)
        rows.append({"cid": cid, "family": fam, "eligible": ok, "n": pooled["n"], "net_r": pooled["net_r"],
                     "win": pooled["win"], "dd": pooled["dd"], "btc_r": per["BTC"]["net_r"], "eth_r": per["ETH"]["net_r"]})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "research" / "STRATEGY_SEARCH.md"))
    ap.add_argument("--reuse", action="store_true", help="reuse the saved trades parquet")
    args = ap.parse_args()
    from delta_intelligence.config.settings import get_settings

    cache = get_settings().data_cache_dir / "strategy_search_trades.parquet"
    t0 = time.time()
    if args.reuse and cache.exists():
        trades = pd.read_parquet(cache)
    else:
        print(f"{len(candidates())} candidates x 3 underlyings x (real + random)", flush=True)
        with ProcessPoolExecutor(max_workers=3) as pool:
            parts = list(pool.map(run_underlying, ["BTCUSD", "ETHUSD", "XAUTUSD"]))
        trades = pd.concat(parts, ignore_index=True)
        trades.to_parquet(cache, index=False)
    disc = select(trades, "real")
    null = select(trades, "random")
    elig = disc[disc["eligible"]].sort_values(["win", "dd"], ascending=[False, False])
    picks, per_fam = [], {}
    for _, r in elig.iterrows():
        if per_fam.get(r["family"], 0) < PER_FAMILY_CAP:
            picks.append(r)
            per_fam[r["family"]] = per_fam.get(r["family"], 0) + 1
        if len(picks) == 10:
            break
    picks = pd.DataFrame(picks)
    hold = trades[(trades["kind"] == "real") & (trades["entry_time"] >= DISCOVERY_END)]
    lines = render(disc, null, picks, hold, trades)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:60]))
    print(f"\nWrote {out} ({time.time() - t0:.0f}s)")
    return 0


def fmt(x, nd=3):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:+.{nd}f}"


def render(disc, null, picks, hold, trades) -> list[str]:
    n_c = disc["cid"].nunique()
    n_e, n_ne = int(disc["eligible"].sum()), int(null["eligible"].sum())
    L = ["# Strategy search: top 10 by win rate and drawdown among positive-R variants", "",
         f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC by `scripts/strategy_search.py`.", "",
         f"- Candidates: **{n_c}** (23 strategies × exit variants × ATM/1-ITM option × normal/2× hold).",
         f"- Discovery: 2025-12-01 → 2026-06-30 on BTC + ETH. Holdout: 2026-07-01 → 2026-10-01 on BTC + ETH + XAUT, "
         "never used for selection.",
         f"- Eligible in discovery (net R > 0 on BTC AND ETH, >= {MIN_TRADES_EACH} trades each): **{n_e} of {n_c}**.",
         f"- **Null benchmark:** the same candidates with RANDOM directions -> **{n_ne} of {n_c}** eligible.",
         "  Picking winners from a list where random signals also 'win' is mostly selecting luck.", "",
         "Net R is per trade, after modelled spread, fees + GST, the breakeven gate and all exits; R = premium paid + "
         "entry fees.", "",
         "## The 10 picks (chosen on discovery only), and how they did on unseen data", "",
         "| # | Candidate | Disc. trades | Disc. win % | Disc. net R | Disc. max DD (R) | Holdout trades | Holdout win % | "
         "Holdout net R | Holdout max DD (R) | Holdout verdict |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    if not len(picks):
        L.append("| - | no candidate was eligible in discovery | | | | | | | | | |")
    surv = 0
    for i, (_, p) in enumerate(picks.iterrows(), 1):
        h = stats(hold[hold["cid"] == p["cid"]])
        ok = h["n"] >= 20 and (h["net_r"] or -1) > 0
        surv += ok
        L.append(f"| {i} | {p['cid']} | {p['n']} | {p['win']:.0%} | {fmt(p['net_r'])} | {fmt(p['dd'], 1)} | {h['n']} | "
                 f"{'-' if h['win'] is None else format(h['win'], '.0%')} | {fmt(h['net_r'])} | {fmt(h['dd'], 1)} | "
                 f"{'positive' if ok else ('too few trades' if h['n'] < 20 else 'NEGATIVE')} |")
    L += ["", f"**{surv} of {len(picks)} picks stayed positive on unseen data** (>= 20 holdout trades).", ""]
    # per-underlying holdout for picks
    L += ["### Holdout by underlying", "", "| Candidate | BTC net R (n) | ETH net R (n) | XAUT net R (n) |", "|---|---|---|---|"]
    for _, p in picks.iterrows():
        cells = []
        for u in ("BTC", "ETH", "XAUT"):
            h = stats(hold[(hold["cid"] == p["cid"]) & (hold["underlying"] == u)])
            cells.append(f"{fmt(h['net_r'])} ({h['n']})")
        L.append(f"| {p['cid']} | " + " | ".join(cells) + " |")
    # null comparison
    if len(null):
        top_null = null[null["eligible"]].sort_values(["win", "dd"], ascending=[False, False]).head(10)
        L += ["", "## What random signals look like in the same search", "",
              "The top random-direction candidates in discovery (same ranking rule):", "",
              "| Candidate (random directions) | Disc. trades | Disc. win % | Disc. net R | Disc. max DD (R) |",
              "|---|---|---|---|---|"]
        for _, r in top_null.iterrows():
            L.append(f"| {r['cid']} | {r['n']} | {r['win']:.0%} | {fmt(r['net_r'])} | {fmt(r['dd'], 1)} |")
    return L + [""]


if __name__ == "__main__":
    sys.exit(main())
