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
from dataclasses import dataclass


@dataclass(frozen=True)
class Underlying:
    perp_symbol: str  # signal candles, funding, OI
    asset: str  # option underlying_asset symbol
    index_symbol: str  # spot index the options settle on


# The perps' `spot_index` field, verified on /v2/products on 2026-10-02.
UNDERLYINGS: dict[str, Underlying] = {
    "BTCUSD": Underlying("BTCUSD", "BTC", ".DEXBTUSD"),
    "ETHUSD": Underlying("ETHUSD", "ETH", ".DEETHUSD"),
}

DEFAULT_WATCHLIST: tuple[str, ...] = ("BTCUSD", "ETHUSD")


def get_watchlist(env_value: str | None = None) -> tuple[str, ...]:
    raw = os.getenv("WATCHLIST", "") if env_value is None else env_value
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    return symbols or DEFAULT_WATCHLIST


def underlying_for(perp_symbol: str) -> Underlying:
    try:
        return UNDERLYINGS[perp_symbol]
    except KeyError:
        raise ValueError(f"{perp_symbol} has no known option underlying/index mapping; add it to UNDERLYINGS "
                         "after verifying its spot_index on /v2/products") from None
