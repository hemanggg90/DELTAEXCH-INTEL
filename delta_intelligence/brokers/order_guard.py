"""
Broker-layer order guard for a BUYING-ONLY options system. Every broker (paper and live) calls `check_order` before
creating ANY order. It is independent of the risk engine, which applies the same rule separately.

Allowed:
- BUY to OPEN (or add to) a long option position;
- SELL to CLOSE an existing long, for at most the contracts currently held.

Rejected (`SellToOpenRejected`):
- any sell with no long position in that symbol, or for more than is held (that would go net short);
- a sell marked as anything other than a close;
- a buy marked as a close (we never hold shorts, so there is nothing to buy back).

Live sells are additionally sent `reduce_only=True` (P7), so the exchange itself refuses anything that would open a
short.
"""
from __future__ import annotations

from delta_intelligence.options.structures import SellToOpenRejected


def check_order(side: str, symbol: str, size: int, purpose: str, held_long_contracts: int) -> None:
    side = side.lower()
    if size <= 0:
        raise ValueError("order size must be positive")
    if side == "buy":
        if purpose != "OPEN":
            raise SellToOpenRejected(f"buy {symbol} with purpose {purpose!r}: there are never shorts to buy back")
        return
    if side != "sell":
        raise ValueError(f"unknown side {side!r}")
    if purpose != "CLOSE":
        raise SellToOpenRejected(f"sell {symbol} with purpose {purpose!r}: selling to open is not allowed")
    if held_long_contracts <= 0:
        raise SellToOpenRejected(f"sell {symbol}: no long position held, so this would open a short")
    if size > held_long_contracts:
        raise SellToOpenRejected(f"sell {size} {symbol}: only {held_long_contracts} held, so this would go net short")
