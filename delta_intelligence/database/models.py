"""
SQLAlchemy models. SQLite for local runs, Postgres via DATABASE_URL for hosted deployments; only portable types are
used.

All datetimes are stored as NAIVE UTC (SQLite has no timezone support). Use `to_db_time` / `from_db_time` at the
boundary.

Trading state lives here, not in Streamlit session state. A position is an option STRUCTURE (1-2 legs), so the
schema is positions -> position_legs, plus orders and fills per leg.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def to_db_time(t) -> dt.datetime | None:
    if t is None:
        return None
    if hasattr(t, "to_pydatetime"):
        t = t.to_pydatetime()
    if t.tzinfo is None:
        raise ValueError("refusing to store a naive datetime: pass tz-aware UTC")
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None)


def from_db_time(t: dt.datetime | None) -> dt.datetime | None:
    return None if t is None else t.replace(tzinfo=dt.timezone.utc)


class MarketDataMetadata(Base):
    __tablename__ = "market_data_metadata"
    id = Column(Integer, primary_key=True)
    instrument = Column(String(48), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False)
    source = Column(String(16), nullable=False)
    start_ts = Column(DateTime)
    end_ts = Column(DateTime)
    n_rows = Column(Integer)
    quality_status = Column(String(16))
    quality_report = Column(JSON)
    fetched_at = Column(DateTime, default=utcnow)


class Decision(Base):
    """One scan result per underlying: the ranking outcome (strategy or NO TRADE) and the setup state."""
    __tablename__ = "decisions"
    id = Column(Integer, primary_key=True)
    decision_id = Column(String(32), unique=True, nullable=False)
    underlying = Column(String(16), nullable=False, index=True)
    bar_time = Column(DateTime, nullable=False, index=True)  # open time of the latest closed bar
    data_quality = Column(String(16))
    regime = Column(String(32))
    regime_confidence = Column(Float)
    selected_strategy = Column(String(64))
    no_trade_reason = Column(Text)
    setup_status = Column(String(24))  # NO_TRADE / WAITING_FOR_SETUP / TRIGGERED
    ranking = Column(JSON)
    created_at = Column(DateTime, default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    position_id = Column(String(32), unique=True, nullable=False)
    mode = Column(String(8), nullable=False)  # PAPER / LIVE
    environment = Column(String(12))  # PRODUCTION / TESTNET
    underlying = Column(String(16), nullable=False, index=True)
    strategy = Column(String(64))
    structure = Column(String(24), nullable=False)
    direction = Column(String(8))
    expiry = Column(DateTime, nullable=False)
    status = Column(String(12), nullable=False, index=True)  # OPEN / CLOSED / SETTLED / STALE
    opened_at = Column(DateTime, default=utcnow)
    closed_at = Column(DateTime)
    contracts = Column(Integer)
    contract_value = Column(Float)
    entry_net_premium = Column(Float)  # USD: >0 paid (debit), <0 received (credit)
    max_loss = Column(Float)  # USD incl. entry fees
    max_profit = Column(Float)  # USD (inf stored as NULL)
    reserved_margin = Column(Float, default=0.0)  # USD blocked for credit spreads
    fees = Column(Float, default=0.0)  # USD incl. GST, all fills so far
    exit_value = Column(Float)  # USD received (or paid) to close
    realized_pnl = Column(Float)
    underlying_stop = Column(Float)
    underlying_target = Column(Float)
    exit_reason = Column(String(32))
    decision_id = Column(String(32))
    risk_decision_id = Column(String(32))
    incomplete = Column(Boolean, default=False)  # a straddle/strangle leg failed; the bought leg was kept
    time_stop_at = Column(DateTime)  # strategy time stop (UTC)
    premium_stop_value = Column(Float)  # USD structure value at which the premium stop fires
    expected_hold_hours = Column(Float)
    notes = Column(Text)


class PositionLeg(Base):
    __tablename__ = "position_legs"
    id = Column(Integer, primary_key=True)
    position_id = Column(String(32), ForeignKey("positions.position_id"), nullable=False, index=True)
    symbol = Column(String(48), nullable=False)
    product_id = Column(Integer)
    kind = Column(String(1), nullable=False)
    strike = Column(Float, nullable=False)
    side = Column(Integer, nullable=False)  # +1 long, -1 short
    contracts = Column(Integer, nullable=False)
    entry_price = Column(Float)
    exit_price = Column(Float)
    entry_iv = Column(Float)
    status = Column(String(12), default="OPEN")  # OPEN / CLOSED / FAILED / SETTLED


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_id = Column(String(32), unique=True, nullable=False)
    client_order_id = Column(String(32), unique=True, nullable=False)  # Delta limit: 32 chars
    exchange_order_id = Column(String(48))
    position_id = Column(String(32), index=True)
    mode = Column(String(8), nullable=False)
    symbol = Column(String(48), nullable=False)
    product_id = Column(Integer)
    side = Column(String(4), nullable=False)  # buy / sell
    size = Column(Integer, nullable=False)
    order_type = Column(String(16), default="limit_order")
    limit_price = Column(Float)
    reduce_only = Column(Boolean, default=False)
    purpose = Column(String(16))  # OPEN / CLOSE / SETTLE
    status = Column(String(16), nullable=False)  # NEW / FILLED / REJECTED / CANCELLED / UNKNOWN
    reject_reason = Column(Text)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    id = Column(Integer, primary_key=True)
    order_id = Column(String(32), ForeignKey("orders.order_id"), nullable=False, index=True)
    price = Column(Float, nullable=False)
    size = Column(Integer, nullable=False)
    fee = Column(Float, default=0.0)  # USD before GST
    gst = Column(Float, default=0.0)
    mid_at_fill = Column(Float)
    timestamp = Column(DateTime, default=utcnow)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id = Column(Integer, primary_key=True)
    decision_id = Column(String(32), nullable=False, index=True)
    event_type = Column(String(16), nullable=False)  # APPROVED / VETO
    reason = Column(Text, nullable=False)
    strategy = Column(String(64))
    underlying = Column(String(16))
    details = Column(JSON)
    created_at = Column(DateTime, default=utcnow, index=True)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True)
    component = Column(String(48), nullable=False)
    level = Column(String(12), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    created_at = Column(DateTime, default=utcnow, index=True)


class EngineState(Base):
    """Small key/value store for durable engine state: kill switch, peak equity, heartbeat, restarts."""
    __tablename__ = "engine_state"
    key = Column(String(64), primary_key=True)
    value = Column(JSON)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id = Column(Integer, primary_key=True)
    title = Column(String(128), nullable=False)
    body_markdown = Column(Text, nullable=False)
    data = Column(JSON)
    created_at = Column(DateTime, default=utcnow)


class ChainSnapshot(Base):
    """Recorded option-chain rows (P5 chain recorder). Builds a REAL bid/ask history over time. Backtests that use it
    are labelled RECORDED_CHAIN."""
    __tablename__ = "chain_snapshots"
    id = Column(Integer, primary_key=True)
    taken_at = Column(DateTime, nullable=False, index=True)
    underlying = Column(String(8), nullable=False, index=True)
    symbol = Column(String(48), nullable=False)
    kind = Column(String(1), nullable=False)
    strike = Column(Float, nullable=False)
    expiry = Column(DateTime, nullable=False)
    spot = Column(Float)
    bid = Column(Float)
    ask = Column(Float)
    bid_size = Column(Float)
    ask_size = Column(Float)
    mark = Column(Float)
    mark_iv = Column(Float)
    delta = Column(Float)
    open_interest = Column(Float)
    volume = Column(Float)
    # ---- Phase 2 additions (all nullable; a missing field stays NULL, never filled in) ------------------------------
    quote_ts_us = Column(BigInteger)  # the exchange's own ticker timestamp (microseconds); detects stale quotes
    dte_days = Column(Float)  # (expiry - taken_at) in days, computed at record time from the two stored timestamps
    gamma = Column(Float)
    theta = Column(Float)
    vega = Column(Float)
    rho = Column(Float)
    bid_iv = Column(Float)
    ask_iv = Column(Float)
    turnover_usd = Column(Float)
    oi_value_usd = Column(Float)
    tick_size = Column(Float)
    contract_value = Column(Float)
    product_id = Column(Integer)
    trading_status = Column(String(24))
    source = Column(String(16), default="REAL_RECORDED")  # REAL_RECORDED. Modelled prices are never written here.
    extra = Column(Text)  # JSON of other scalar fields the API exposed that have no column (robust to API changes)
    __table_args__ = (Index("ix_chain_underlying_symbol_taken", "underlying", "symbol", "taken_at"),)
