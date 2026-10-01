"""
Instruments the system scans.

The default is BTC, ETH, SOL and XRP perpetuals. All four were verified live on India production and testnet on
2026-10-01; see docs/DELTA_API_NOTES.md section 4. Override with `WATCHLIST=BTCUSD,ETHUSD,...`.

Contract specs (contract_value, tick_size, fees, margins) are NOT hard-coded here. They are read from /v2/products at
runtime, and product ids are resolved per environment.
"""
from __future__ import annotations

import os

DEFAULT_WATCHLIST: tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


def get_watchlist(env_value: str | None = None) -> tuple[str, ...]:
    raw = os.getenv("WATCHLIST", "") if env_value is None else env_value
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    return symbols or DEFAULT_WATCHLIST
