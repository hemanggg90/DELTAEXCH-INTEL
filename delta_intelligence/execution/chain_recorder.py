"""
Option-chain recorder: stores snapshots of the whole exposed ticker (bid, ask, sizes, mark, IV, all Greeks, OI, volume,
spot, the exchange's own timestamp) every N minutes per underlying, so a REAL option-price history accumulates. Until
enough has accumulated, backtests model premiums from inferred IV; recorded rows are labelled REAL_RECORDED and are
never mixed with modelled values (modelled prices are never written to this table).

Rules:
- **Never fabricate.** A field the API did not send (or sent empty/non-numeric) is stored as NULL. Zero and negative
  numbers are stored as received so the data-quality layer can see and report them; consumers decide how to treat them.
- **Robust to API changes.** Known fields are read defensively from the raw ticker; any other scalar fields are kept
  in `extra` (JSON) so nothing the API exposes is lost.
- **Idempotent per snapshot.** A (symbol, taken_at) pair is written once; recording the same snapshot twice adds nothing.
- Only strikes within +-`band` of spot and expiries at most `max_days` away are kept, to bound the table. Timestamps
  are UTC.
"""
from __future__ import annotations

import datetime as dt
import json
import math

import pandas as pd

from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot, to_db_time
from delta_intelligence.options.chain import OptionChain

_KNOWN_TOP = {"symbol", "strike_price", "mark_price", "oi", "oi_contracts", "volume", "timestamp", "quotes", "greeks",
              "product_id", "tick_size", "contract_value", "turnover_usd", "oi_value_usd", "product_trading_status",
              "spot_price", "underlying_asset_symbol", "contract_type", "description", "time", "tags", "price_band",
              "turnover_symbol", "oi_value_symbol", "close", "high", "low", "open", "size", "turnover"}


def num(x) -> float | None:
    """A finite float, else None. Strings are parsed; '', None and non-numbers become None (never 0)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def integer(x) -> int | None:
    v = num(x)
    return None if v is None else int(v)


def extra_fields(raw: dict) -> str | None:
    """Scalar fields with no column of their own, as JSON (nested quote/greek extras included under their section)."""
    out: dict = {}
    for k, v in raw.items():
        if k in _KNOWN_TOP:
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
    for sect, known in (("quotes", {"best_bid", "best_ask", "bid_size", "ask_size", "mark_iv", "bid_iv", "ask_iv"}),
                        ("greeks", {"delta", "gamma", "theta", "vega", "rho", "spot"})):
        d = raw.get(sect) or {}
        extra = {f"{sect}.{k}": v for k, v in d.items() if k not in known and isinstance(v, (int, float, str, bool))}
        out.update(extra)
    for k in ("close", "high", "low", "open", "size", "turnover"):
        if isinstance(raw.get(k), (int, float, str)):
            out[k] = raw[k]
    return json.dumps(out, sort_keys=True) if out else None


def normalize_raw(raw: dict | None) -> dict:
    """Map a raw Delta option ticker to ChainSnapshot columns. Missing or malformed fields become None."""
    if not raw:
        return {}
    q, g = raw.get("quotes") or {}, raw.get("greeks") or {}
    return {
        "quote_ts_us": integer(raw.get("timestamp")), "gamma": num(g.get("gamma")), "theta": num(g.get("theta")),
        "vega": num(g.get("vega")), "rho": num(g.get("rho")), "bid_iv": num(q.get("bid_iv")),
        "ask_iv": num(q.get("ask_iv")), "turnover_usd": num(raw.get("turnover_usd")),
        "oi_value_usd": num(raw.get("oi_value_usd")), "tick_size": num(raw.get("tick_size")),
        "contract_value": num(raw.get("contract_value")), "product_id": integer(raw.get("product_id")),
        "trading_status": (str(raw["product_trading_status"])[:24] if raw.get("product_trading_status") else None),
        "extra": extra_fields(raw),
    }


def snapshot_rows(chain: OptionChain, underlying: str, spot: float, now: dt.datetime, band: float = 0.10,
                  max_days: float = 45.0) -> list[dict]:
    rows = []
    now_ts = pd.Timestamp(now)
    limit = now_ts + pd.Timedelta(days=max_days)
    for q in chain.by_symbol.values():
        if q.underlying != underlying or q.expiry > limit or q.expiry <= now_ts:
            continue
        if spot and abs(q.strike / spot - 1) > band:
            continue
        row = {"underlying": underlying, "symbol": q.symbol, "kind": q.kind, "strike": q.strike,
               "expiry": q.expiry, "spot": q.spot or spot, "bid": q.bid, "ask": q.ask, "bid_size": q.bid_size,
               "ask_size": q.ask_size, "mark": q.mark, "mark_iv": q.mark_iv, "delta": q.delta,
               "open_interest": q.open_interest, "volume": q.volume,
               "dte_days": (q.expiry - now_ts).total_seconds() / 86400.0, "source": "REAL_RECORDED"}
        row.update(normalize_raw(q.raw))
        if row.get("quote_ts_us") is None:
            row["quote_ts_us"] = integer(q.timestamp_us)
        rows.append(row)
    return rows


def record(chain: OptionChain, spots: dict[str, float], now: dt.datetime, band: float = 0.10) -> int:
    """Persist one snapshot for each underlying in `spots`. Returns the number of NEW rows written (a (symbol,
    taken_at) pair already stored is skipped, so recording the same snapshot twice is harmless)."""
    n = 0
    taken = to_db_time(now)
    with db.get_session() as s:
        have = {sym for (sym,) in s.query(ChainSnapshot.symbol).filter(ChainSnapshot.taken_at == taken).all()}
        for u, spot in spots.items():
            for r in snapshot_rows(chain, u, spot, now, band):
                if r["symbol"] in have:
                    continue
                have.add(r["symbol"])
                s.add(ChainSnapshot(taken_at=taken, **{**r, "expiry": to_db_time(r["expiry"])}))
                n += 1
    return n
