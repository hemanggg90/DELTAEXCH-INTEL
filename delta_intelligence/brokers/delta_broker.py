"""
LIVE broker for a BUYING-ONLY options system, on Delta Exchange India (TESTNET unless DELTA_ENV says otherwise).

Same interface as `PaperBroker`, so the engine, planner, monitor and risk engine are unchanged.

**Gates.** Construction fails unless BOTH `TRADING_MODE=LIVE` and `TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK`
are set (re-checked here, independently of the app) and API credentials exist. `brokers/live_gate.py` additionally
requires the connectivity check (time sync, auth, IP whitelist) to pass before a broker is handed out.

**Orders.** Every order passes `order_guard.check_order` first: buy-to-open, or sell-to-close up to the held size.
- Entries: IOC limit buy at best ask + `SLIPPAGE_TICKS` ticks, so nothing is left resting on the book.
- Exits: IOC limit sell at best bid - slippage, always `reduce_only=true`, so the exchange itself refuses a short.
- The order row is committed as NEW **before** the request is sent. A crash or timeout therefore always leaves a
  record to reconcile.
- Orders are throttled but never retried. If a send fails in transit (`OrderStateUnknownError`) the broker looks the
  order up by `client_order_id`. If that still can't settle it, the order is stored UNKNOWN and the kill switch is
  engaged: new trades stop, exits keep running, and `reconcile()` resolves it.
- No exchange-side brackets: exits are managed by the engine (underlying invalidation, premium stop, time stop,
  expiry guard).

**Fills.** The order response is read defensively (`unfilled_size`, `average_fill_price`, `paid_commission`). Their
exact shape on options is UNVERIFIED until confirmed on testnet. Where the response omits a price, the limit price
is used as a conservative stand-in and the order notes say so. Where it omits the fee, the fallback fee model is used.

**Money.** `cash` is the USD wallet's available balance. equity = wallet balance + open positions at mid
(UNVERIFIED that the wallet balance excludes option value; confirm on testnet).

**Settlement.** The exchange settles expired longs itself. `settle_position` only records the result once the
exchange no longer holds the leg. The recorded P&L uses the modelled settlement index (labelled).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pandas as pd

from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.brokers.errors import DeltaError, OrderRejectedError, OrderStateUnknownError
from delta_intelligence.brokers.order_guard import check_order
from delta_intelligence.brokers.paper_broker import (
    BUCKET_OF,
    DEFAULT_TICK,
    OpenResult,
    TradePlan,
    client_order_id,
    new_id,
)
from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, Settings, get_settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg, from_db_time, to_db_time
from delta_intelligence.options.chain import OptionChain
from delta_intelligence.options.structures import Leg, Structure, assert_buy_only, leg_fee
from delta_intelligence.utils.logging_utils import log_event
from delta_intelligence.utils.timeutil import now_utc

TERMINAL_STATES = ("closed", "cancelled", "filled")


class LiveNotAuthorizedError(RuntimeError):
    """LIVE was requested without both gates, or without credentials."""


def _f(x, default: float | None = None) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def price_string(price: float, tick: float, buy: bool) -> str:
    """Price on the tick grid (buys round up, sells round down), as the string Delta wants."""
    t = Decimal(str(tick))
    d = Decimal(str(price))
    n = (d / t).to_integral_value(ROUND_CEILING if buy else ROUND_FLOOR)
    out = max(n * t, t)
    return format(out.normalize(), "f")


class DeltaBroker:
    mode = "LIVE"
    name = "delta"

    def __init__(self, chain: Callable[[], OptionChain], settings: Settings | None = None,
                 client: DeltaClient | None = None, clock: Callable[[], object] = now_utc):
        s = settings or get_settings()
        if not s.is_live_mode or s.trading_live_confirm != LIVE_CONFIRM_PHRASE:
            raise LiveNotAuthorizedError("LIVE needs TRADING_MODE=LIVE and TRADING_LIVE_CONFIRM="
                                         f"{LIVE_CONFIRM_PHRASE}")
        if not s.has_credentials:
            raise LiveNotAuthorizedError("LIVE needs DELTA_API_KEY and DELTA_API_SECRET")
        if client is None:
            client = DeltaClient(s.rest_base_url, s.delta_api_key, s.delta_api_secret, environment=s.delta_env,
                                 limits_settings=s, allow_orders=True)
        if not client.allow_orders:
            raise LiveNotAuthorizedError("the DeltaClient was not built with allow_orders=True")
        if client.base_url != s.rest_base_url.rstrip("/"):
            raise LiveNotAuthorizedError("the client's base URL does not match DELTA_ENV")
        self.settings, self.chain, self.client, self.clock = s, chain, client, clock
        self._lock = threading.RLock()
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    # ---- exchange reads ------------------------------------------------------------------------------------------
    def _usd_wallet(self) -> dict | None:
        for b in self.client.get_wallet_balances():
            if (b.get("asset_symbol") or "").upper() in ("USD", "USDT"):
                return b
        return None

    @property
    def cash(self) -> float:
        w = self._usd_wallet()
        return _f((w or {}).get("available_balance"), 0.0) or 0.0

    # ---- one order -----------------------------------------------------------------------------------------------
    def _limit(self, q, buy: bool) -> tuple[float | None, float]:
        tick = q.tick_size or DEFAULT_TICK.get(q.underlying, 0.01)
        slip = self.settings.costs.slippage_ticks * tick
        if buy:
            return (None if not q.ask or q.ask <= 0 else q.ask + slip), tick
        return (None if not q.bid or q.bid <= 0 else max(q.bid - slip, tick)), tick

    def _send_leg(self, position_id: str, leg: Leg, buy: bool, cv: float, purpose: str, chain: OptionChain,
                  held_long: int = 0) -> tuple[int, float, float] | None:
        """Send one IOC limit order. Returns (filled contracts, average price, fee incl. GST), or None when nothing
        filled. Raises nothing for an ordinary rejection; an unresolvable timeout engages the kill switch."""
        check_order("buy" if buy else "sell", leg.symbol, leg.contracts, purpose, held_long)  # FIRST, always
        q = chain.quote(leg.symbol)
        limit, tick = self._limit(q, buy) if q else (None, 0.01)
        oid, coid = new_id("ORD"), client_order_id()
        row = dict(order_id=oid, client_order_id=coid, position_id=position_id, mode=self.mode, symbol=leg.symbol,
                   product_id=q.product_id if q else None, side="buy" if buy else "sell", size=leg.contracts,
                   order_type="limit_order", limit_price=limit, purpose=purpose, reduce_only=not buy)
        if q is None or limit is None or not q.product_id:
            with db.get_session() as s:
                s.add(Order(**row, status="REJECTED", reject_reason="no two-sided quote or product id"))
            return None
        with db.get_session() as s:  # committed BEFORE sending: a crash leaves a record to reconcile
            s.add(Order(**row, status="NEW"))
        body = {"product_id": int(q.product_id), "size": int(leg.contracts), "side": "buy" if buy else "sell",
                "order_type": "limit_order", "limit_price": price_string(limit, tick, buy), "time_in_force": "ioc",
                "reduce_only": not buy, "client_order_id": coid}
        note = None
        try:
            resp = self.client.place_order(body)
        except OrderRejectedError as exc:
            self._update_order(oid, "REJECTED", reject_reason=str(exc))
            log_event("delta_broker", f"order rejected: {exc}", level="WARNING", symbol=leg.symbol, code=exc.code)
            return None
        except OrderStateUnknownError as exc:
            resp = self._resolve_unknown(oid, coid, exc)
            if resp is None:
                return None
            note = "resolved by client_order_id after a timeout"
        except DeltaError as exc:  # rate limit, auth, config: nothing was sent
            self._update_order(oid, "REJECTED", reject_reason=f"{type(exc).__name__}: {exc}")
            log_event("delta_broker", f"order not sent: {exc}", level="ERROR", symbol=leg.symbol)
            return None
        return self._apply_response(oid, leg, buy, cv, q, limit, resp or {}, note)

    def _update_order(self, oid: str, status: str, **fields) -> None:
        with db.get_session() as s:
            o = s.query(Order).filter_by(order_id=oid).one()
            o.status = status
            for k, v in fields.items():
                setattr(o, k, v)

    def _resolve_unknown(self, oid: str, coid: str, exc: Exception) -> dict | None:
        try:
            found = self.client.get_order_by_client_id(coid)
        except DeltaError:
            found = None
        if found:
            return found.get("result", found) if isinstance(found, dict) and "state" not in found else found
        # Not found (or no answer): a 404 right after a timeout is not proof the order never reached the matching
        # engine, so this is UNKNOWN, never "rejected". reconcile() settles it later.
        self._update_order(oid, "UNKNOWN", reject_reason=f"send failed in transit: {exc}")
        self._connected = False
        from delta_intelligence.execution.engine import set_kill_switch

        set_kill_switch(True, f"order {coid} state unknown; run reconcile before trading")
        return None

    def _apply_response(self, oid: str, leg: Leg, buy: bool, cv: float, q, limit: float, resp: dict,
                        note: str | None) -> tuple[int, float, float] | None:
        size = int(_f(resp.get("size"), leg.contracts) or leg.contracts)
        unfilled = int(_f(resp.get("unfilled_size"), 0 if resp.get("state") == "closed" else size) or 0)
        filled = max(0, min(size, size - unfilled))
        exch_id = str(resp["id"]) if resp.get("id") is not None else None
        if filled == 0:
            self._update_order(oid, "CANCELLED", exchange_order_id=exch_id, reject_reason=note or "IOC: no fill")
            return None
        avg = _f(resp.get("average_fill_price"))
        notes = [note] if note else []
        if not avg or avg <= 0:
            avg = limit
            notes.append("fill price missing in response; limit price used (conservative)")
        fee = _f(resp.get("paid_commission"))
        if fee is None:
            c = self.settings.costs
            fee = leg_fee(avg, q.spot or 0.0, filled * cv, c.fallback_taker_rate, c.fallback_premium_cap_rate, 0.0)
            fee *= 1 + c.gst_rate
            notes.append("fee estimated (paid_commission missing)")
        self._update_order(oid, "FILLED", exchange_order_id=exch_id, reject_reason="; ".join(notes) or None)
        with db.get_session() as s:
            s.add(Fill(order_id=oid, price=avg, size=filled, fee=fee, gst=0.0, mid_at_fill=q.mid,
                       timestamp=to_db_time(self.clock())))
        return filled, avg, fee

    # ---- open / close / settle -----------------------------------------------------------------------------------
    def open_structure(self, plan: TradePlan) -> OpenResult:
        st = plan.structure
        assert_buy_only(st.legs)
        if any(not leg.symbol for leg in st.legs):
            return OpenResult(False, reason="every leg needs a symbol")
        with self._lock:
            chain = self.chain()
            pid = new_id("POS")
            filled: list[tuple[Leg, int, float]] = []
            fees, fills_out = 0.0, []
            for leg in st.legs:
                r = self._send_leg(pid, leg, True, st.contract_value, "OPEN", chain)
                if r is None:
                    if not filled:
                        return OpenResult(False, reason=f"{leg.symbol} did not fill")
                    continue  # a later straddle/strangle leg failed: keep the bought leg
                filled.append((leg, r[0], r[1]))
                fees += r[2]
                fills_out.append((leg.symbol, 1, r[1], r[2]))
            incomplete = len(filled) < len(st.legs) or any(n < leg.contracts for leg, n, _ in filled)
            kept_legs = [Leg(leg.kind, leg.strike, leg.expiry, 1, n, leg.symbol) for leg, n, _ in filled]
            name = st.name if len(filled) == len(st.legs) else ("LONG_CALL" if filled[0][0].kind == "C" else "LONG_PUT")
            direction = st.direction if len(filled) == len(st.legs) else ("LONG" if name == "LONG_CALL" else "SHORT")
            kept = Structure(name, st.underlying, kept_legs, st.contract_value, direction)
            prices = [p for _, _, p in filled]
            paid = kept.premium_paid(prices)
            mp = kept.max_profit(prices)
            with db.get_session() as s:
                s.add(Position(
                    position_id=pid, mode=self.mode, environment=self.settings.delta_env, underlying=st.underlying,
                    strategy=plan.strategy, structure=kept.name, direction=direction,
                    expiry=to_db_time(pd.Timestamp(st.expiry)), status="OPEN", opened_at=to_db_time(self.clock()),
                    contracts=kept_legs[0].contracts, contract_value=st.contract_value, entry_net_premium=paid,
                    max_loss=paid + fees, max_profit=None if mp == float("inf") else mp, reserved_margin=0.0,
                    fees=fees, underlying_stop=plan.underlying_stop, underlying_target=plan.underlying_target,
                    decision_id=plan.decision_id, risk_decision_id=plan.risk_decision_id, incomplete=incomplete,
                    time_stop_at=to_db_time(plan.time_stop_at) if plan.time_stop_at is not None else None,
                    premium_stop_value=paid * (1 - plan.premium_stop_pct / 100) if plan.premium_stop_pct else None,
                    expected_hold_hours=plan.expected_hold_hours,
                    notes="a leg did not fill completely; what was bought was kept" if incomplete else None))
                for leg in st.legs:
                    hit = next(((n, p) for lg, n, p in filled if lg is leg), None)
                    s.add(PositionLeg(position_id=pid, symbol=leg.symbol, kind=leg.kind, strike=leg.strike, side=1,
                                      contracts=hit[0] if hit else leg.contracts, entry_price=hit[1] if hit else None,
                                      status="OPEN" if hit else "FAILED"))
            log_event("delta_broker", f"bought {kept.name} {st.underlying} ({plan.strategy})", position_id=pid,
                      premium=round(paid, 2), fees=round(fees, 2), incomplete=incomplete, env=self.settings.delta_env)
            return OpenResult(True, pid, "filled" if not incomplete else "partially filled; bought part kept",
                              incomplete, fills_out)

    def close_position(self, position_id: str, reason: str) -> float | None:
        """Sell-to-close every held leg (reduce-only IOC). Partial fills shrink the leg; the position stays OPEN until
        every leg is flat, and the engine retries next cycle. Returns realised P&L when fully closed, else None."""
        with self._lock:
            chain = self.chain()
            with db.get_session() as s:
                pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
                if pos is None:
                    return None
                cv, expiry = pos.contract_value, from_db_time(pos.expiry)
                legs = [(lg.id, lg.symbol, lg.kind, lg.strike, lg.contracts)
                        for lg in s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all()]
            all_flat = True
            for lid, symbol, kind, strike, contracts in legs:
                leg = Leg(kind, strike, expiry, 1, contracts, symbol)
                r = self._send_leg(position_id, leg, False, cv, "CLOSE", chain, held_long=contracts)
                with db.get_session() as s:
                    lg = s.get(PositionLeg, lid)
                    pos = s.query(Position).filter_by(position_id=position_id).one()
                    if r is None:
                        all_flat = False
                        continue
                    n, price, fee = r
                    pos.fees = (pos.fees or 0.0) + fee
                    pos.exit_value = (pos.exit_value or 0.0) + price * n * pos.contract_value  # proceeds so far
                    lg.exit_price = price  # last fill price (informational; P&L uses the accumulated proceeds)
                    if n >= lg.contracts:
                        lg.status = "CLOSED"
                    else:
                        lg.contracts -= n
                        all_flat = False
                        pos.notes = ((pos.notes or "") + f" partial close {n} of {symbol};").strip()
            if not all_flat:
                log_event("delta_broker", "sell-to-close incomplete; will retry next cycle", level="WARNING",
                          position_id=position_id)
                return None
            with db.get_session() as s:
                pos = s.query(Position).filter_by(position_id=position_id).one()
                return self._finalise(s, pos, "CLOSED", reason)

    def settle_position(self, position_id: str, settlement_index: float) -> float | None:
        """Record an expiry the exchange has already settled. Waits (returns None) while the exchange still shows
        the leg. P&L uses the MODELLED settlement index; reconcile against the wallet for the exact amount."""
        with self._lock:
            held = {p.get("product_symbol") or (p.get("product") or {}).get("symbol")
                    for p in self.client.get_positions() if _f(p.get("size"), 0.0)}
            with db.get_session() as s:
                pos = s.query(Position).filter_by(position_id=position_id, status="OPEN").one_or_none()
                if pos is None:
                    return None
                legs = s.query(PositionLeg).filter_by(position_id=position_id, status="OPEN").all()
                if any(lg.symbol in held for lg in legs):
                    return None
                for lg in legs:
                    lg.exit_price = (max(settlement_index - lg.strike, 0.0) if lg.kind == "C"
                                     else max(lg.strike - settlement_index, 0.0))
                    lg.status = "SETTLED"
                    pos.exit_value = (pos.exit_value or 0.0) + lg.exit_price * lg.contracts * pos.contract_value
                pos.notes = ((pos.notes or "") + " settlement P&L modelled from the index TWAP;").strip()
                return self._finalise(s, pos, "SETTLED", f"settled at index {settlement_index:.2f} (modelled)")

    def _finalise(self, s, pos: Position, status: str, reason: str) -> float:
        """Realised P&L = everything received (accumulated in exit_value) - premium paid - all fees."""
        pos.realized_pnl = (pos.exit_value or 0.0) - (pos.entry_net_premium or 0.0) - (pos.fees or 0.0)
        pos.status, pos.exit_reason, pos.closed_at = status, reason, to_db_time(self.clock())
        log_event("delta_broker", f"{status.lower()} {pos.structure} {pos.underlying}: P&L ${pos.realized_pnl:,.2f}",
                  position_id=pos.position_id, reason=reason)
        return float(pos.realized_pnl)

    # ---- views (same shapes as PaperBroker) -------------------------------------------------------------------------
    def open_positions(self) -> list[dict]:
        with db.get_session() as s:
            return [{"position": p, "legs": s.query(PositionLeg).filter_by(position_id=p.position_id,
                                                                           status="OPEN").all()}
                    for p in s.query(Position).filter_by(status="OPEN", mode=self.mode).all()]

    def mark(self, item: dict, chain: OptionChain | None = None) -> tuple[float | None, float | None]:
        chain = chain or self.chain()
        p, legs = item["position"], item["legs"]
        value = basis = 0.0
        for lg in legs:
            q = chain.quote(lg.symbol)
            if q is None or q.mid is None or lg.entry_price is None:
                return None, None
            units = lg.contracts * p.contract_value
            value += q.mid * units
            basis += lg.entry_price * units
        return value, value - basis

    def account_snapshot(self) -> dict:
        w = self._usd_wallet() or {}
        self._connected = True
        balance = _f(w.get("balance"), 0.0) or 0.0
        available = _f(w.get("available_balance"), balance) or 0.0
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
        return {"cash": balance, "available_cash": available, "open_value": open_value,
                "equity": balance + open_value, "open_positions": len(items), "unpriced_positions": unknown,
                "exposure_by_strategy": exposure, "exposure_by_bucket": bucket,
                "total_exposure": sum(exposure.values())}

    # ---- reconciliation ----------------------------------------------------------------------------------------------
    def reconcile(self) -> dict:
        """Compare the database with the exchange. Read-only on the exchange.

        - UNKNOWN / NEW orders are looked up by client_order_id and resolved when the exchange knows them.
        - A long on the exchange that the database doesn't have (ORPHAN), a SHORT on the exchange, a size that
          differs, or an order still unresolved are CRITICAL: the kill switch is engaged, nothing is auto-traded.
        - A database leg the exchange no longer holds (CLOSED_ELSEWHERE) marks the position STALE.
        Returns {"ok", "critical": [...], "notes": [...]}.
        """
        critical: list[str] = []
        notes: list[str] = []
        with self._lock:
            with db.get_session() as s:
                pending = [(o.order_id, o.client_order_id, o.symbol) for o in
                           s.query(Order).filter(Order.mode == self.mode, Order.status.in_(("UNKNOWN", "NEW"))).all()]
            for oid, coid, symbol in pending:
                try:
                    found = self.client.get_order_by_client_id(coid)
                except DeltaError as exc:
                    critical.append(f"order {coid} ({symbol}) can't be looked up: {exc}")
                    continue
                if found is None:
                    self._update_order(oid, "REJECTED", reject_reason="exchange has no record (never accepted)")
                    notes.append(f"order {coid} ({symbol}): not on the exchange")
                    continue
                unfilled = _f(found.get("unfilled_size"))
                size = _f(found.get("size"))
                state = found.get("state")
                self._update_order(oid, "FILLED" if (unfilled is not None and size is not None and unfilled < size)
                                   else "CANCELLED", exchange_order_id=str(found.get("id")),
                                   reject_reason=f"resolved at reconcile (state={state}); adopt via positions below")
                notes.append(f"order {coid} ({symbol}): exchange state {state}")
            ex_rows = [p for p in self.client.get_positions() if _f(p.get("size"), 0.0)]
            ex: dict[str, float] = {}
            for p in ex_rows:
                sym = p.get("product_symbol") or (p.get("product") or {}).get("symbol") or str(p.get("product_id"))
                ex[sym] = _f(p.get("size"), 0.0) or 0.0
            for sym, size in ex.items():
                if size < 0:
                    critical.append(f"SHORT position on the exchange: {sym} size {size}")
            with db.get_session() as s:
                db_legs: dict[str, int] = {}
                for lg, pos in (s.query(PositionLeg, Position).join(
                        Position, Position.position_id == PositionLeg.position_id)
                        .filter(Position.mode == self.mode, Position.status == "OPEN",
                                PositionLeg.status == "OPEN").all()):
                    db_legs[lg.symbol] = db_legs.get(lg.symbol, 0) + lg.contracts
                for sym, n in db_legs.items():
                    got = ex.get(sym, 0.0)
                    if got <= 0:
                        notes.append(f"CLOSED_ELSEWHERE: {sym} ({n} in database, none on the exchange)")
                        for pos in s.query(Position).filter_by(mode=self.mode, status="OPEN").all():
                            if any(l.symbol == sym for l in s.query(PositionLeg).filter_by(
                                    position_id=pos.position_id, status="OPEN").all()):
                                pos.status = "STALE"
                                pos.notes = ((pos.notes or "") + " not on the exchange at reconcile;").strip()
                    elif got != n:
                        critical.append(f"SIZE MISMATCH {sym}: database {n}, exchange {got}")
                for sym, size in ex.items():
                    if size > 0 and sym not in db_legs:
                        critical.append(f"ORPHAN long on the exchange: {sym} size {size} (not in the database)")
        ok = not critical
        if not ok:
            from delta_intelligence.execution.engine import set_kill_switch

            set_kill_switch(True, "reconcile mismatch: " + "; ".join(critical)[:300])
        log_event("delta_broker", "reconcile finished", level="INFO" if ok else "ERROR", critical=critical,
                  notes=notes)
        return {"ok": ok, "critical": critical, "notes": notes, "exchange_positions": ex}
