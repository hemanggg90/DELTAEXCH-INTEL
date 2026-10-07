"""
Build the live ranker's evidence: every strategy in the universe, backtested as BOUGHT options on each underlying with the
project's option market model (MODEL_ONLY), saved as one compact table.

    python scripts/build_ranker_evidence.py                      # BTC, ETH, XAUT since 2025-12-01
    python scripts/build_ranker_evidence.py --assets BTC --strategies "Squeeze Breakout"   # a quick subset

Writes delta_intelligence/evidence/ranker_evidence.parquet (+ a .json note). Commit that file so a deployed app has the
evidence. It needs the local candle and IV-history caches (see scripts/fetch_candles.py, scripts/build_iv_history.py).
The evidence is MODELED (no recorded option-chain history exists yet); the ranker's thresholds are unchanged, so a
strategy without a demonstrated edge still ranks NO TRADE.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from delta_intelligence.analogues.analogue_engine import COMPARISON_FEATURES  # noqa: E402
from delta_intelligence.backtesting.option_backtest import OptionBacktestConfig, RealOptionPrices, run_option_backtest  # noqa: E402
from delta_intelligence.backtesting.option_market import OptionMarketModel  # noqa: E402
from delta_intelligence.config.events import load_events  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import underlying_for  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.features.feature_engine import compute_features  # noqa: E402
from delta_intelligence.features.inputs import load_feature_inputs  # noqa: E402
from delta_intelligence.options.iv_history import IvHistoryStore, fit_smile  # noqa: E402
from delta_intelligence.options.selector import SelectorConfig  # noqa: E402
from delta_intelligence.ranking.live_ranker import EVIDENCE_COLUMNS, EVIDENCE_PATH  # noqa: E402
from delta_intelligence.research.ranking_core import observations_from_trades  # noqa: E402
from delta_intelligence.strategies.universe import UNIVERSE, build_universe_frame  # noqa: E402

PERPS = {"BTC": "BTCUSD", "ETH": "ETHUSD", "XAUT": "XAUTUSD"}


def load_market(store: IvHistoryStore, asset: str, index: pd.DataFrame):
    meta = pd.read_parquet(store.meta_path())
    meta = meta[meta["underlying"] == asset]
    hourly = pd.read_parquet(store.root / "iv_obs" / f"{asset}_atm_hourly.parquet")
    obs = pd.read_parquet(store.obs_path(asset), columns=["known_at", "t_hours", "log_moneyness", "iv"])
    smile = fit_smile(obs, hourly)
    strikes = {e: np.sort(g["strike"].unique()) for e, g in meta.groupby("expiry")}
    symbols = {(k, s, e): sym for k, s, e, sym in zip(meta["kind"], meta["strike"], meta["expiry"], meta["symbol"])}

    def loader(expiry):
        p = store.expiry_path(asset, expiry)
        return pd.read_parquet(p) if p.exists() else None

    return OptionMarketModel(asset, index, hourly, smile, strikes, symbols), RealOptionPrices(loader), hourly


def build_asset(asset: str, since: str, keys: list[str] | None) -> pd.DataFrame:
    s = get_settings()
    dm = DataManager.from_settings(s)
    store = IvHistoryStore(s.data_cache_dir / "options" / s.data_env.lower())
    start = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc)
    inp = load_feature_inputs(dm, PERPS[asset], "5m", start - dt.timedelta(days=3))
    feats = compute_features(inp.ohlcv, "5m", inp.aux)
    market, _, hourly = load_market(store, underlying_for(PERPS[asset]).asset, inp.aux.index)
    frame = build_universe_frame(inp.ohlcv, feats, hourly, load_events())
    keep = (frame["timestamp"] >= pd.Timestamp(start)).to_numpy()
    frame, feats = frame[keep].reset_index(drop=True), feats[keep].reset_index(drop=True)
    rows = []
    for cand in UNIVERSE:
        if keys and cand.key not in keys:
            continue
        strat = cand.build()
        res = run_option_backtest(strat, frame, market, OptionBacktestConfig(selector=SelectorConfig(moneyness=cand.moneyness)))
        if not res.trades:
            print(f"  {asset} {cand.key:48} setups {res.n_setups:5} trades     0", flush=True)
            continue
        obs = observations_from_trades(res.trades, feats, cand.key)
        obs["asset"] = asset
        obs["exit_timestamp"] = [t.exit_time for t in res.trades]
        rows.append(obs)
        print(f"  {asset} {cand.key:48} setups {res.n_setups:5} trades {len(res.trades):5} net R {obs['r_multiple'].mean():+.3f}", flush=True)
    return pd.concat(rows, ignore_index=True)[EVIDENCE_COLUMNS] if rows else pd.DataFrame(columns=EVIDENCE_COLUMNS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", nargs="+", default=list(PERPS), choices=list(PERPS))
    ap.add_argument("--since", default="2025-12-01")
    ap.add_argument("--strategies", nargs="*", default=None, help="restrict to these strategy keys")
    ap.add_argument("--out", default=str(EVIDENCE_PATH))
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    t0 = time.time()
    parts = []
    with ProcessPoolExecutor(max_workers=min(args.workers, len(args.assets))) as ex:
        futs = {a: ex.submit(build_asset, a, args.since, args.strategies) for a in args.assets}
        for a, f in futs.items():
            parts.append(f.result())
    ev = pd.concat(parts, ignore_index=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(out, index=False)
    note = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "since": args.since, "assets": args.assets,
            "rows": int(len(ev)), "strategies": int(ev["strategy_name"].nunique()), "pricing": "MODELED (MODEL_ONLY backtest)",
            "per_strategy": ev.groupby("strategy_name").agg(n=("r_multiple", "size"), net_r=("r_multiple", "mean")).round(4).to_dict("index")}
    out.with_suffix(".json").write_text(json.dumps(note, indent=2), encoding="utf-8")
    print(f"\nwrote {out} ({len(ev):,} trades, {ev['strategy_name'].nunique()} strategies, {time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
