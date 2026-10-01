"""
Paper broker for defined-risk option structures, filled against REAL Delta quotes.

**Fills**
- Each leg fills at the live best ask (buys) or best bid (sells), `SLIPPAGE_TICKS` ticks worse. A leg with no
  two-sided quote is not filled.
- **Open: long leg first.** If a spread's short leg then cannot fill, the long leg is kept, so the position is still
  defined risk. It is flagged `incomplete` and its max loss is recomputed.
- **Close: short leg first** (buy it back), then sell the long leg. Risk stays defined at every step.

**Settlement:** positions still open at expiry settle at intrinsic value against the settlement index (the 30-min
index TWAP, verified). No settlement fee is charged (Delta's settlement fee is UNVERIFIED).

**Money (USD, as the exchange settles)**
- `cash` moves on every fill: debit paid, credit received, fees + GST.
- A credit spread reserves its max loss as margin. Delta's actual margin rule for option spreads is UNVERIFIED; the
  max loss is the conservative choice.
- available = cash − reserved margin; equity = cash + open positions valued at mid.

Everything (orders, fills, positions, legs, cash) is persisted, so the book survives restarts. `client_order_id`s are
unique and at most 32 characters (Delta's limit), so the same records work for live reconciliation.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from delta_intelligence.config.settings import Settings, get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg, from_db_time, to_db_time
from delta_intelligence.options.chain import OptionChain, OptionQuote
from delta_intelligence.options.structures import Leg, Structure, leg_fee
from delta_intelligence.utils.logging_utils import log_event
from delta_intelligence.utils.timeutil import now_utc

CASH_KEY = "paper_cash_usd"
DEFAULT_TICK = {"BTC": 0.1, "ETH": 0.01}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def client_order_id() -> str:
    return f"di{uuid.uuid4().hex[:30]}"  # 32 chars


@dataclass
class TradePlan:
    strategy: str
    structure: Structure  # legs carry symbol + contracts
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
        tick = DEFAULT_TICK.get(q.underlying, 0.01)
        slip = self.settings.costs.slippage_ticks * tick
        if buy:
            return None if not q.ask or q.ask <= 0 else round(q.ask + slip, 8)
        return None if not q.bid or q.bid <= 0 else round(max(q.bid - slip, 0.0), 8)

    def _fee(self, price: float, spot: float, units: float) -> tuple[float, float]:
        c = self.settings.costs
        fee = leg_fee(price, spot, units, c.fallback_taker_rate, c.fallback_premium_cap_rate, 0.0)
        return fee, fee * c.gst_rate

    def _fill_leg(self, session, position_id: str, leg: Leg, buy: bool, cv: float, purpose: str,
                  chain: OptionChain) -> tuple[float, float] | None:
        """Fill one leg at the live quote. Returns (price, fee incl. GST), or None if it can't fill (order REJECTED)."""
        q = chain.quote(leg.symbol)
        price = self._price(q, buy) if q else None
        oid = new_id("ORD")
        session.add(Order(order_id=oid, client_order_id=client_order_id(), position_id=position_id, mode=self.mode,
                          symbol=leg.symbol, product_id=q.product_id if q else None, side="buy" if buy else "sell",
                          size=leg.contracts, order_type="limit_order", limit_price=price, purpose=purpose,
                          reduce_only=purpose == "CLOSE", status="FILLED" if price is not None else "REJECTED",
                          reject_reason=None if price is not None else "no two-sided quote"))
        if price is None:
            return None
        units = leg.contracts * cv
        fee, gst = self._fee(price, q.spot or 0.0, units)
        session.add(Fill(order_id=oid, price=price, size=leg.contracts, fee=fee, gst=gst, mid_at_fill=q.mid,
                         timestamp=to_db_time(self.clock())))
        return price, fee + gst

    # ---- open / close ---------------------------------------------------------------------------------------------
    def open_structure(self, plan: TradePlan) -> OpenResult:
        st = plan.structure
        if any(not leg.symbol for leg in st.legs):
            return OpenResult(False, reason="every leg needs a symbol")
        with self._lock:
            chain = self.chain()
            pid = new_id("POS")
            ordered = sorted(st.legs, key=lambda leg: -leg.side)  # long legs first
            filled: list[tuple[Leg, float]] = []
            fees = 0.0
            fills_out = []
            with db.get_session() as s:
                for leg in ordered:
                    r = self._fill_leg(s, pid, leg, leg.side > 0, st.contract_value, "OPEN", chain)
                    if r is None:
                        if leg.side > 0 and not filled:
                            log_event("paper_broker", f"open aborted: long leg {leg.symbol} not fillable", level="WARNING")
                            return OpenResult(False, reason=f"long leg {leg.symbol} has no two-sided quote")
                        if leg.side > 0:
                            # A LATER long leg failed: unwinding is more defined than holding a partial. Not reachable for
                            # verticals (one long leg); kept for safety.
                            return OpenResult(False, reason=f"long leg {leg.symbol} failed")
                        continue  # short leg failed: keep the long leg(s)
                    filled.append((leg, r[0]))
                    fees += r[1]
                    fills_out.append((leg.symbol, leg.side, r[0], r[1]))
                kept = Structure(st.name if len(filled) == len(st.legs) else
                                 ("LONG_CALL" if filled[0][0].kind == "C" else "LONG_PUT"),
                                 st.underlying, [leg for leg, _ in filled], st.contract_value, st.direction)
                prices = [p for _, p in filled]
                incomplete = len(filled) < len(st.legs)
                net = kept.net_premium(prices)
                max_loss = kept.max_loss(prices) + fees
                credit = net < 0
                reserved = max_loss if credit else 0.0
                mp = kept.max_profit(prices)
                s.add(Position(
                    position_id=pid, mode=self.mode, environment=self.settings.data_env, underlying=st.underlying,
                    strategy=plan.strategy, structure=kept.name, direction=st.direction,
                    expiry=to_db_time(pd.Timestamp(st.expiry)), status="OPEN", opened_at=to_db_time(self.clock()),
                    contracts=st.legs[0].contracts, contract_value=st.contract_value, entry_net_premium=net,
                    max_loss=max_loss, max_profit=None if mp == float("inf") else mp, reserved_margin=reserved,
                    fees=fees, underlying_stop=plan.underlying_stop, underlying_target=plan.underlying_target,
                    decision_id=plan.decision_id, risk_decision_id=plan.risk_decision_id, incomplete=incomplete,
                    notes="short leg could not fill; kept the long leg (still defined risk)" if incomplete else None))
                for leg in st.legs:
                    price = next((p for lg, p in filled if lg is leg), None)
                    s.add(PositionLeg(position_id=pid, symbol=leg.symbol, kind=leg.kind, strike=leg.strike,
                                      side=leg.side, contracts=leg.contracts, entry_price=price,
                                      status="OPEN" if price is not None else "FAILED"))
            self._add_cash(-net - fees)
            log_event("paper_broker", f"opened {kept.name} {st.underlying} ({plan.strategy})", position_id=pid,
                      net_premium=round(net, 2), max_loss=round(max_loss, 2), incomplete=incomplete)
            return OpenResult(True, pid, "filled" if not incomplete else "short leg failed; long kept", incomplete,
                              fills_out)

    def close_position(self, position_id: str, reason: str) -> float | None:
        """Close at live quotes, short legs first. Returns realised P&L, or None if a leg couldn't be closed (the
        position stays OPEN, already-closed legs are recorded, and the caller retries next cycle)."""
        with self._lock:
            chain = self.chain()
            with db.get_session() as s:
                pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
                if pos is None:
                    return None
                legs = s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all()
                legs.sort(key=lambda lg: lg.side)  # shorts (-1) first
                for lg in legs:
                    leg = Leg(lg.kind, lg.strike, from_db_time(pos.expiry), lg.side, lg.contracts, lg.symbol)
                    r = self._fill_leg(s, position_id, leg, lg.side < 0, pos.contract_value, "CLOSE", chain)
                    if r is None:
                        log_event("paper_broker", f"close of {lg.symbol} failed (no quote); will retry", level="WARNING",
                                  position_id=position_id)
                        return None
                    lg.exit_price, lg.status = r[0], "CLOSED"
                    pos.fees = (pos.fees or 0.0) + r[1]
                    self._add_cash(lg.side * r[0] * lg.contracts * pos.contract_value - r[1])
                return self._finalise(s, pos, "CLOSED", reason)

    def settle_position(self, position_id: str, settlement_index: float) -> float | None:
        """Cash-settle at intrinsic value against the settlement index (no fee: UNVERIFIED)."""
        with self._lock, db.get_session() as s:
            pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
            if pos is None:
                return None
            for lg in s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all():
                intrinsic = max(settlement_index - lg.strike, 0.0) if lg.kind == "C" else max(lg.strike - settlement_index, 0.0)
                lg.exit_price, lg.status = intrinsic, "SETTLED"
                self._add_cash(lg.side * intrinsic * lg.contracts * pos.contract_value)
            return self._finalise(s, pos, "SETTLED", f"settled at index {settlement_index:.2f}")

    def _finalise(self, s, pos: Position, status: str, reason: str) -> float:
        legs = s.query(PositionLeg).filter_by(position_id=pos.position_id).all()
        gross = sum(lg.side * (lg.exit_price - lg.entry_price) * lg.contracts * pos.contract_value
                    for lg in legs if lg.entry_price is not None and lg.exit_price is not None)
        pos.realized_pnl = gross - (pos.fees or 0.0)
        pos.exit_value = sum(lg.side * lg.exit_price * lg.contracts * pos.contract_value
                             for lg in legs if lg.exit_price is not None)
        pos.status, pos.exit_reason, pos.closed_at = status, reason, to_db_time(self.clock())
        pos.reserved_margin = 0.0
        log_event("paper_broker", f"{status.lower()} {pos.structure} {pos.underlying}: P&L ${pos.realized_pnl:,.2f}",
                  position_id=pos.position_id, reason=reason)
        return float(pos.realized_pnl)

    # ---- views -----------------------------------------------------------------------------------------------------
    def open_positions(self) -> list[dict]:
        with db.get_session() as s:
            out = []
            for p in s.query(Position).filter_by(status="OPEN", mode=self.mode).all():
                legs = s.query(PositionLeg).filter_by(position_id=p.position_id, status="OPEN").all()
                out.append({"position": p, "legs": legs})
            return out

    def mark(self, item: dict, chain: OptionChain | None = None) -> tuple[float | None, float | None]:
        """(current value at mid, unrealised P&L before exit fees). None if any leg lacks a quote."""
        chain = chain or self.chain()
        p, legs = item["position"], item["legs"]
        value = 0.0
        basis = 0.0
        for lg in legs:
            q = chain.quote(lg.symbol)
            if q is None or q.mid is None:
                return None, None
            units = lg.contracts * p.contract_value
            value += lg.side * q.mid * units
            basis += lg.side * lg.entry_price * units
        return value, value - basis

    def account_snapshot(self) -> dict:
        chain = self.chain()
        items = self.open_positions()
        open_value, unknown = 0.0, 0
        exposure: dict[str, float] = {}
        reserved = 0.0
        for it in items:
            v, _ = self.mark(it, chain)
            p = it["position"]
            if v is None:
                unknown += 1
                v = -p.reserved_margin if p.entry_net_premium < 0 else 0.0  # conservative when unpriced
            open_value += v
            exposure[p.strategy] = exposure.get(p.strategy, 0.0) + (p.max_loss or 0.0)
            reserved += p.reserved_margin or 0.0
        cash = self.cash
        return {"cash": cash, "reserved_margin": reserved, "available_cash": cash - reserved,
                "open_value": open_value, "equity": cash + open_value, "open_positions": len(items),
                "unpriced_positions": unknown, "exposure_by_strategy": exposure,
                "total_exposure": sum(exposure.values())}
