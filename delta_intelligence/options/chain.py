"""
Live option chain from Delta's tickers. `/v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=
BTC,ETH` returns the whole chain in ONE request (weight 3, verified 2026-10-02) and is shared via the ticker cache.

Option tickers carry no settlement_time, so expiry is parsed from the symbol (DDMMYY at 12:00 UTC).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

OPTION_TYPES = "call_options,put_options"


def expiry_from_symbol(symbol: str) -> pd.Timestamp:
    """Expiry from a C|P-ASSET-STRIKE-DDMMYY symbol. The settlement HOUR depends on the underlying (BTC/ETH 12:00 UTC,
    XAUT 16:00 UTC, verified); prefer `settlement_time` from /v2/products when available."""
    from delta_intelligence.config.watchlist import SETTLE_HOUR_BY_ASSET

    asset, d = symbol.split("-")[1], symbol.split("-")[-1]
    hour = SETTLE_HOUR_BY_ASSET.get(asset, 12)
    return pd.Timestamp(dt.datetime(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]), hour, tzinfo=dt.timezone.utc))


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


@dataclass
class OptionQuote:
    symbol: str
    underlying: str
    kind: str
    strike: float
    expiry: pd.Timestamp
    bid: float | None
    ask: float | None
    mark: float | None
    mark_iv: float | None
    open_interest: float | None
    bid_size: float | None
    ask_size: float | None
    delta: float | None
    spot: float | None
    product_id: int | None
    contract_value: float | None
    timestamp_us: int | None
    volume: float | None = None  # 24h traded contracts
    tick_size: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.mark


def parse_ticker(t: dict) -> OptionQuote | None:
    sym = t.get("symbol", "")
    if not sym or sym[0] not in "CP" or sym.count("-") != 3:
        return None
    q, g = t.get("quotes") or {}, t.get("greeks") or {}
    return OptionQuote(
        symbol=sym, underlying=sym.split("-")[1], kind=sym[0], strike=float(t["strike_price"]),
        expiry=expiry_from_symbol(sym), bid=_f(q.get("best_bid")), ask=_f(q.get("best_ask")),
        mark=_f(t.get("mark_price")), mark_iv=_f(q.get("mark_iv")), open_interest=_f(t.get("oi_contracts") or t.get("oi")),
        bid_size=_f(q.get("bid_size")), ask_size=_f(q.get("ask_size")), delta=_f(g.get("delta")), spot=_f(g.get("spot")),
        product_id=t.get("product_id"), contract_value=_f(t.get("contract_value")), timestamp_us=t.get("timestamp"),
        volume=_f(t.get("volume")), tick_size=_f(t.get("tick_size")))


class OptionChain:
    def __init__(self, quotes: list[OptionQuote]):
        self.by_symbol = {q.symbol: q for q in quotes}

    @classmethod
    def from_tickers(cls, tickers: dict[str, dict] | list[dict]) -> "OptionChain":
        rows = tickers.values() if isinstance(tickers, dict) else tickers
        return cls([q for q in (parse_ticker(t) for t in rows) if q is not None])

    def quote(self, symbol: str) -> OptionQuote | None:
        return self.by_symbol.get(symbol)

    def expiries(self, underlying: str) -> list[pd.Timestamp]:
        return sorted({q.expiry for q in self.by_symbol.values() if q.underlying == underlying})

    def strikes(self, underlying: str, expiry: pd.Timestamp) -> np.ndarray:
        return np.array(sorted({q.strike for q in self.by_symbol.values()
                                if q.underlying == underlying and q.expiry == expiry}))

    def symbol(self, underlying: str, kind: str, strike: float, expiry: pd.Timestamp) -> str | None:
        for q in self.by_symbol.values():
            if q.underlying == underlying and q.kind == kind and q.strike == strike and q.expiry == expiry:
                return q.symbol
        return None

    def atm_iv(self, underlying: str, expiry: pd.Timestamp, spot: float) -> float | None:
        cands = [q for q in self.by_symbol.values() if q.underlying == underlying and q.expiry == expiry and q.mark_iv]
        if not cands:
            return None
        best = min(cands, key=lambda q: abs(q.strike - spot))
        same = [q.mark_iv for q in cands if q.strike == best.strike]
        return float(np.mean(same))


def fetch_chain(client, underlyings: tuple[str, ...] = ("BTC", "ETH"), max_age: float | None = None) -> OptionChain:
    """One cached request for the whole chain. `client` is a DeltaClient."""
    payload = client.ticker_cache.get(
        ("option_chain", client.base_url, underlyings),
        lambda: client._result("GET", "/v2/tickers", params={"contract_types": OPTION_TYPES,
                                                             "underlying_asset_symbols": ",".join(underlyings)},
                               weight=3) or [],
        client.settings.delta.ticker_cache_ttl_sec if max_age is None else max_age)
    return OptionChain.from_tickers(payload)
