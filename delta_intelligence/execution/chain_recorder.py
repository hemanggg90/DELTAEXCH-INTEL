"""
Option-chain recorder: stores snapshots (bid, ask, sizes, IV, delta, OI, volume, spot) every N minutes per underlying,
so a REAL option-price history accumulates. Today the backtest has to model premiums from inferred IV; recorded rows
replace that model over time.

To keep the table small it records strikes within ±`band` of spot, for expiries at most `max_days` away. Timestamps
are UTC.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot, to_db_time
from delta_intelligence.options.chain import OptionChain


def snapshot_rows(chain: OptionChain, underlying: str, spot: float, now: dt.datetime, band: float = 0.10,
                  max_days: float = 45.0) -> list[dict]:
    rows = []
    limit = pd.Timestamp(now) + pd.Timedelta(days=max_days)
    for q in chain.by_symbol.values():
        if q.underlying != underlying or q.expiry > limit or q.expiry <= pd.Timestamp(now):
            continue
        if spot and abs(q.strike / spot - 1) > band:
            continue
        rows.append({"underlying": underlying, "symbol": q.symbol, "kind": q.kind, "strike": q.strike,
                     "expiry": q.expiry, "spot": q.spot or spot, "bid": q.bid, "ask": q.ask, "bid_size": q.bid_size,
                     "ask_size": q.ask_size, "mark": q.mark, "mark_iv": q.mark_iv, "delta": q.delta,
                     "open_interest": q.open_interest, "volume": q.volume})
    return rows


def record(chain: OptionChain, spots: dict[str, float], now: dt.datetime, band: float = 0.10) -> int:
    """Persist one snapshot for each underlying in `spots`. Returns the number of rows written."""
    n = 0
    taken = to_db_time(now)
    with db.get_session() as s:
        for u, spot in spots.items():
            for r in snapshot_rows(chain, u, spot, now, band):
                s.add(ChainSnapshot(taken_at=taken, **{**r, "expiry": to_db_time(r["expiry"])}))
                n += 1
    return n
