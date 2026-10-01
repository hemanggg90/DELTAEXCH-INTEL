"""
Instruments the system scans.

The system BUYS OPTIONS ONLY (user decision, 2026-10-02). The watchlist lists the **underlyings**: their perpetual
symbols supply signal candles, and their options are what get bought. Delta India lists options on BTC, ETH and XAUT
(no SOL), and all three are the default. Override with e.g. `WATCHLIST=BTCUSD,ETHUSD`.

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
    settle_hour_utc: int = 12  # option settlement hour (verified 2026-10-02)
    correlated_bucket: str = ""  # underlyings in the same bucket share the combined-premium cap


# The perps' `spot_index` field, verified on /v2/products on 2026-10-02.
UNDERLYINGS: dict[str, Underlying] = {
    "BTCUSD": Underlying("BTCUSD", "BTC", ".DEXBTUSD", 12, "CRYPTO_MAJORS"),
    "ETHUSD": Underlying("ETHUSD", "ETH", ".DEETHUSD", 12, "CRYPTO_MAJORS"),
    "XAUTUSD": Underlying("XAUTUSD", "XAUT", ".DEXAUTUSD", 16, "GOLD"),
}
SETTLE_HOUR_BY_ASSET: dict[str, int] = {u.asset: u.settle_hour_utc for u in UNDERLYINGS.values()}

# User decision 2026-10-02: trade BTC, ETH and XAUT options (Delta India lists no SOL options).
DEFAULT_WATCHLIST: tuple[str, ...] = ("BTCUSD", "ETHUSD", "XAUTUSD")


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
