"""
Instruments the system scans.

The system trades OPTIONS ONLY (user decision, 2026-10-02). The watchlist lists the **underlyings**: their perpetual
symbols supply signal candles, and their options are what get traded. Only BTC and ETH have options on Delta India
(XAUT does too, but is not selected), so they are the default. Override with `WATCHLIST=BTCUSD,ETHUSD`.

Contract specs (contract_value, tick_size, fees, margins) are NOT hard-coded here. They are read from /v2/products at
runtime, and product ids are resolved per environment.
"""
from __future__ import annotations

import os

DEFAULT_WATCHLIST: tuple[str, ...] = ("BTCUSD", "ETHUSD")


def get_watchlist(env_value: str | None = None) -> tuple[str, ...]:
    raw = os.getenv("WATCHLIST", "") if env_value is None else env_value
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    return symbols or DEFAULT_WATCHLIST
