"""
Run the 24x7 trading engine headless (the P5 48-hour gate test, or a VPS without the dashboard).

    python scripts/run_engine.py                 # Ctrl+C to stop

- PAPER (default): public production market data, no API keys.
- LIVE: only with TRADING_MODE=LIVE and TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK plus keys for DELTA_ENV
  (testnet by default). The startup gate (connectivity + IP whitelist) must pass and the engine reconciles with
  the exchange before its first trade. Market data then comes from the trading venue.
- Trades only the ACTIVE variants (strategies/active.py) through the risk engine.
- Records option-chain snapshots every 5 minutes.
- Prints a status line every 5 minutes. Everything is persisted in the database (default data_cache/).
- The process must not sleep: disable sleep on this PC, or run it on a VPS.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from delta_intelligence.brokers.delta_api_client import public_client  # noqa: E402
from delta_intelligence.brokers.paper_broker import PaperBroker  # noqa: E402
from delta_intelligence.brokers.ws_feed import TickerFeed  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.database import db  # noqa: E402
from delta_intelligence.execution.engine import TradingEngine  # noqa: E402
from delta_intelligence.options.chain import fetch_chain  # noqa: E402
from delta_intelligence.utils.timeutil import fmt_ist, now_utc  # noqa: E402


def iv_history(settings) -> dict:
    out = {}
    root = settings.data_cache_dir / "options" / settings.data_env.lower() / "iv_obs"
    for u in UNDERLYINGS.values():
        p = root / f"{u.asset}_atm_hourly.parquet"
        if p.exists():
            h = pd.read_parquet(p).dropna(subset=["atm_iv_6_30h"])
            out[u.asset] = pd.Series(h["atm_iv_6_30h"].to_numpy(), index=pd.to_datetime(h["available_at"], utc=True))
    return out


def build_engine():
    s = get_settings()
    if s.is_live_mode and not s.live_mode_fully_authorized:
        raise SystemExit("TRADING_MODE=LIVE also needs TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK; refusing")
    db.init_db()
    client = public_client(s)
    assets = tuple(UNDERLYINGS[p].asset for p in get_watchlist())
    chain_fn = lambda: fetch_chain(client, assets, max_age=15)  # noqa: E731
    if s.is_live_mode:
        from delta_intelligence.brokers.live_gate import LiveGateError, build_live_broker

        try:
            broker = build_live_broker(chain_fn, s)
        except LiveGateError as exc:
            raise SystemExit(str(exc))
    else:
        broker = PaperBroker(chain_fn, s)
    feed = TickerFeed(s.ws_url(private=False), list(get_watchlist()))
    return TradingEngine(broker, chain_fn, DataManager.from_settings(s), s,
                         feed=feed, iv_history=iv_history(s))


def main() -> int:
    eng = build_engine()
    eng.start()
    print(f"engine started {fmt_ist(now_utc())} - {eng.broker.mode} - variants: {', '.join(v.key for v in eng.variants)}", flush=True)
    try:
        while True:
            time.sleep(300)
            st = eng.status()
            snap = eng.broker.account_snapshot()
            print(f"{fmt_ist(now_utc())} running={st['running']} cycles={st['cycles']} errors={st['cycle_errors']} "
                  f"ws={st['ws_status']} equity=${snap['equity']:,.2f} open={snap['open_positions']} "
                  f"last_error={st['last_error']}", flush=True)
            for asset, d in st["last_decisions"].items():
                print(f"   {asset}: {d['status']} - {d['detail'][:160]}", flush=True)
    except KeyboardInterrupt:
        eng.stop()
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
