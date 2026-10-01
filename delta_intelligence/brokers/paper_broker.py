"""
Paper broker for a BUYING-ONLY options system, filled against REAL Delta quotes.

Every order first passes `order_guard.check_order`: only buy-to-open, or sell-to-close up to the held long size.
Selling to open is impossible here, independently of the risk engine.

**Fills:** each leg fills at the live best ask (buy) or best bid (sell), `SLIPPAGE_TICKS` ticks worse. A leg with no
two-sided quote is not filled.

**Open / close**
- Open: every leg is bought. If a straddle/strangle leg can't fill after the first did, the bought leg is kept (still
  a long option), flagged `incomplete`, and its max loss is recomputed.
- Close: every held long leg is sold for exactly the held size.

**Settlement:** positions still open at expiry settle at intrinsic value against the settlement index (30-min index
TWAP, verified). No settlement fee is charged (UNVERIFIED).

**Money (USD):**
- `cash` moves on every fill: premium paid / received, fees + GST.
- Bought options need no margin: available = cash.
- equity = cash + open positions valued at mid.

Everything (orders, fills, positions, legs, cash) is persisted, so the book survives restarts. `client_order_id`s are
unique and at most 32 characters (Delta's limit).
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from delta_intelligence.brokers.order_guard import check_order
from delta_intelligence.config.settings import Settings, get_settings
from delta_intelligence.config.watchlist import UNDERLYINGS
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg, from_db_time, to_db_time
from delta_intelligence.options.chain import OptionChain, OptionQuote
from delta_intelligence.options.structures import Leg, Structure, assert_buy_only, leg_fee
from delta_intelligence.utils.logging_utils import log_event
from delta_intelligence.utils.timeutil import now_utc

CASH_KEY = "paper_cash_usd"
DEFAULT_TICK = {"BTC": 0.1, "ETH": 0.01, "XAUT": 0.01}
BUCKET_OF = {u.asset: u.correlated_bucket for u in UNDERLYINGS.values()}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def client_order_id() -> str:
    return f"di{uuid.uuid4().hex[:30]}"  # 32 chars


@dataclass
class TradePlan:
    strategy: str
    structure: Structure  # bought legs, each with symbol + contracts
    underlying_stop: float | None = None
    underlying_target: float | None = None
    decision_id: str | None = None
    risk_decision_id: str | None = None


@dataclass
class OpenResult:
    ok: bool
    position_id: str | None = None
    reason: str = ""
    incomplete: bool = False
    fills: list = field(default_factory=list)  # (symbol, side, price, fee_incl_gst)


class PaperBroker:
    mode = "PAPER"
    name = "paper"

    def __init__(self, chain: Callable[[], OptionChain], settings: Settings | None = None,
                 clock: Callable[[], object] = now_utc):
        self.chain = chain
        self.settings = settings or get_settings()
        self.clock = clock
        self._lock = threading.RLock()

    def is_connected(self) -> bool:
        return True

    # ---- cash --------------------------------------------------------------------------------------------------
    @property
    def cash(self) -> float:
        v = db.get_state(CASH_KEY)
        if v is None:
            v = self.settings.paper_starting_capital_usd
            db.set_state(CASH_KEY, v)
        return float(v)

    def _add_cash(self, delta: float) -> None:
        db.set_state(CASH_KEY, self.cash + delta)

    # ---- fills ---------------------------------------------------------------------------------------------------
    def _price(self, q: OptionQuote, buy: bool) -> float | None:
        tick = q.tick_size or DEFAULT_TICK.get(q.underlying, 0.01)
        slip = self.settings.costs.slippage_ticks * tick
        if buy:
            return None if not q.ask or q.ask <= 0 else round(q.ask + slip, 8)
        return None if not q.bid or q.bid <= 0 else round(max(q.bid - slip, 0.0), 8)

    def _fill_leg(self, session, position_id: str, leg: Leg, buy: bool, cv: float, purpose: str,
                  chain: OptionChain, held_long: int = 0) -> tuple[float, float] | None:
        """Fill one leg. The order guard runs FIRST and raises on anything but buy-to-open / sell-to-close."""
        check_order("buy" if buy else "sell", leg.symbol, leg.contracts, purpose, held_long)
        q = chain.quote(leg.symbol)
        price = self._price(q, buy) if q else None
        oid = new_id("ORD")
        session.add(Order(order_id=oid, client_order_id=client_order_id(), position_id=position_id, mode=self.mode,
                          symbol=leg.symbol, product_id=q.product_id if q else None, side="buy" if buy else "sell",
                          size=leg.contracts, order_type="limit_order", limit_price=price, purpose=purpose,
                          reduce_only=not buy, status="FILLED" if price is not None else "REJECTED",
                          reject_reason=None if price is not None else "no two-sided quote"))
        if price is None:
            return None
        units = leg.contracts * cv
        c = self.settings.costs
        fee = leg_fee(price, q.spot or 0.0, units, c.fallback_taker_rate, c.fallback_premium_cap_rate, 0.0)
        gst = fee * c.gst_rate
        session.add(Fill(order_id=oid, price=price, size=leg.contracts, fee=fee, gst=gst, mid_at_fill=q.mid,
                         timestamp=to_db_time(self.clock())))
        return price, fee + gst

    # ---- open / close / settle ------------------------------------------------------------------------------------
    def open_structure(self, plan: TradePlan) -> OpenResult:
        st = plan.structure
        assert_buy_only(st.legs)  # structures can't hold short legs; asserted again here on purpose
        if any(not leg.symbol for leg in st.legs):
            return OpenResult(False, reason="every leg needs a symbol")
        with self._lock:
            chain = self.chain()
            pid = new_id("POS")
            filled: list[tuple[Leg, float]] = []
            fees = 0.0
            fills_out = []
            with db.get_session() as s:
                for leg in st.legs:
                    r = self._fill_leg(s, pid, leg, True, st.contract_value, "OPEN", chain)
                    if r is None:
                        if not filled:
                            log_event("paper_broker", f"open aborted: {leg.symbol} not fillable", level="WARNING")
                            return OpenResult(False, reason=f"{leg.symbol} has no two-sided quote")
                        continue  # a later leg of a straddle/strangle failed: keep the bought leg
                    filled.append((leg, r[0]))
                    fees += r[1]
                    fills_out.append((leg.symbol, 1, r[0], r[1]))
                incomplete = len(filled) < len(st.legs)
                name = st.name if not incomplete else ("LONG_CALL" if filled[0][0].kind == "C" else "LONG_PUT")
                direction = st.direction if not incomplete else ("LONG" if name == "LONG_CALL" else "SHORT")
                kept = Structure(name, st.underlying, [leg for leg, _ in filled], st.contract_value, direction)
                prices = [p for _, p in filled]
                paid = kept.premium_paid(prices)
                mp = kept.max_profit(prices)
                s.add(Position(
                    position_id=pid, mode=self.mode, environment=self.settings.data_env, underlying=st.underlying,
                    strategy=plan.strategy, structure=kept.name, direction=direction,
                    expiry=to_db_time(pd.Timestamp(st.expiry)), status="OPEN", opened_at=to_db_time(self.clock()),
                    contracts=st.legs[0].contracts, contract_value=st.contract_value, entry_net_premium=paid,
                    max_loss=paid + fees, max_profit=None if mp == float("inf") else mp, reserved_margin=0.0,
                    fees=fees, underlying_stop=plan.underlying_stop, underlying_target=plan.underlying_target,
                    decision_id=plan.decision_id, risk_decision_id=plan.risk_decision_id, incomplete=incomplete,
                    notes="a leg could not fill; the bought leg was kept" if incomplete else None))
                for leg in st.legs:
                    price = next((p for lg, p in filled if lg is leg), None)
                    s.add(PositionLeg(position_id=pid, symbol=leg.symbol, kind=leg.kind, strike=leg.strike, side=1,
                                      contracts=leg.contracts, entry_price=price,
                                      status="OPEN" if price is not None else "FAILED"))
            self._add_cash(-paid - fees)
            log_event("paper_broker", f"bought {kept.name} {st.underlying} ({plan.strategy})", position_id=pid,
                      premium=round(paid, 2), fees=round(fees, 2), incomplete=incomplete)
            return OpenResult(True, pid, "filled" if not incomplete else "one leg failed; bought leg kept",
                              incomplete, fills_out)

    def close_position(self, position_id: str, reason: str) -> float | None:
        """Sell every held long leg at live quotes. Returns realised P&L, or None if a leg couldn't be sold (the
        position stays OPEN, legs already sold are recorded, and the caller retries next cycle)."""
        with self._lock:
            chain = self.chain()
            with db.get_session() as s:
                pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
                if pos is None:
                    return None
                for lg in s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all():
                    leg = Leg(lg.kind, lg.strike, from_db_time(pos.expiry), 1, lg.contracts, lg.symbol)
                    r = self._fill_leg(s, position_id, leg, False, pos.contract_value, "CLOSE", chain,
                                       held_long=lg.contracts)
                    if r is None:
                        log_event("paper_broker", f"sell-to-close of {lg.symbol} failed (no bid); will retry",
                                  level="WARNING", position_id=position_id)
                        return None
                    lg.exit_price, lg.status = r[0], "CLOSED"
                    pos.fees = (pos.fees or 0.0) + r[1]
                    self._add_cash(r[0] * lg.contracts * pos.contract_value - r[1])
                return self._finalise(s, pos, "CLOSED", reason)

    def settle_position(self, position_id: str, settlement_index: float) -> float | None:
        with self._lock, db.get_session() as s:
            pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
            if pos is None:
                return None
            for lg in s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all():
                intrinsic = (max(settlement_index - lg.strike, 0.0) if lg.kind == "C"
                             else max(lg.strike - settlement_index, 0.0))
                lg.exit_price, lg.status = intrinsic, "SETTLED"
                self._add_cash(intrinsic * lg.contracts * pos.contract_value)
            return self._finalise(s, pos, "SETTLED", f"settled at index {settlement_index:.2f}")

    def _finalise(self, s, pos: Position, status: str, reason: str) -> float:
        legs = s.query(PositionLeg).filter_by(position_id=pos.position_id).all()
        gross = sum((lg.exit_price - lg.entry_price) * lg.contracts * pos.contract_value
                    for lg in legs if lg.entry_price is not None and lg.exit_price is not None)
        pos.realized_pnl = gross - (pos.fees or 0.0)
        pos.exit_value = sum(lg.exit_price * lg.contracts * pos.contract_value for lg in legs if lg.exit_price is not None)
        pos.status, pos.exit_reason, pos.closed_at = status, reason, to_db_time(self.clock())
        log_event("paper_broker", f"{status.lower()} {pos.structure} {pos.underlying}: P&L ${pos.realized_pnl:,.2f}",
                  position_id=pos.position_id, reason=reason)
        return float(pos.realized_pnl)

    # ---- views -----------------------------------------------------------------------------------------------------
    def open_positions(self) -> list[dict]:
        with db.get_session() as s:
            return [{"position": p, "legs": s.query(PositionLeg).filter_by(position_id=p.position_id,
                                                                           status="OPEN").all()}
                    for p in s.query(Position).filter_by(status="OPEN", mode=self.mode).all()]

    def mark(self, item: dict, chain: OptionChain | None = None) -> tuple[float | None, float | None]:
        """(value at mid, unrealised P&L before exit fees). None if any leg lacks a quote."""
        chain = chain or self.chain()
        p, legs = item["position"], item["legs"]
        value = basis = 0.0
        for lg in legs:
            q = chain.quote(lg.symbol)
            if q is None or q.mid is None:
                return None, None
            units = lg.contracts * p.contract_value
            value += q.mid * units
            basis += lg.entry_price * units
        return value, value - basis

    def account_snapshot(self) -> dict:
        chain = self.chain()
        items = self.open_positions()
        open_value, unknown = 0.0, 0
        exposure: dict[str, float] = {}
        bucket: dict[str, float] = {}
        for it in items:
            v, _ = self.mark(it, chain)
            p = it["position"]
            if v is None:
                unknown += 1
                v = 0.0  # an unpriced long option is counted at zero (conservative)
            open_value += v
            exposure[p.strategy] = exposure.get(p.strategy, 0.0) + (p.max_loss or 0.0)
            b = BUCKET_OF.get(p.underlying, "")
            if b:
                bucket[b] = bucket.get(b, 0.0) + (p.max_loss or 0.0)
        cash = self.cash
        return {"cash": cash, "available_cash": cash, "open_value": open_value, "equity": cash + open_value,
                "open_positions": len(items), "unpriced_positions": unknown, "exposure_by_strategy": exposure,
                "exposure_by_bucket": bucket, "total_exposure": sum(exposure.values())}
