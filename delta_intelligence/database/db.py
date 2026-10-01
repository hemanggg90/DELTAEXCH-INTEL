"""
Database engine and session management.

- The engine is created LAZILY from `DATABASE_URL` (SQLite in data_cache/ by default, Postgres when configured).
  Importing this module touches no database.
- `init_db()` creates missing tables, then adds any model columns missing from existing tables. This is an additive
  migration, never a drop or retype, ported from the reference.
- The persistence helpers used by logging and data loading (`persist_system_event`, `persist_market_data_metadata`)
  write only after `init_db()` has run in this process, and they never raise.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from delta_intelligence.database.models import Base, EngineState, MarketDataMetadata, SystemEvent, to_db_time

_lock = threading.Lock()
_engine: Engine | None = None
_Session = None
_initialized = False


def get_engine(url: str | None = None) -> Engine:
    global _engine, _Session
    with _lock:
        if _engine is None:
            if url is None:
                from delta_intelligence.config.settings import get_settings

                url = get_settings().database_url
            if url.startswith("sqlite:///"):
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
            _engine = create_engine(url, **kwargs)
            _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests, or after DATABASE_URL changes)."""
    global _engine, _Session, _initialized
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _Session, _initialized = None, None, False


def _sync_schema(engine: Engine) -> list[str]:
    """ALTER TABLE ... ADD COLUMN for model columns missing from existing tables. Returns what was added."""
    added = []
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ctype = col.type.compile(dialect=engine.dialect)
                name = engine.dialect.identifier_preparer.quote(col.name)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {name} {ctype}"))
                added.append(f"{table.name}.{col.name}")
    return added


def init_db(url: str | None = None) -> list[str]:
    global _initialized
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    added = _sync_schema(engine)
    _initialized = True
    return added


def is_initialized() -> bool:
    return _initialized


@contextmanager
def get_session():
    if _Session is None:
        get_engine()
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---- best-effort persistence used by logging / data layers --------------------------------------------------------
def persist_system_event(component: str, level: str, message: str, details=None) -> None:
    if not _initialized:
        return
    try:
        with get_session() as s:
            s.add(SystemEvent(component=component, level=level, message=message, details=details))
    except Exception:
        pass


def persist_market_data_metadata(meta: dict) -> None:
    if not _initialized:
        return
    try:
        with get_session() as s:
            s.add(MarketDataMetadata(
                instrument=meta["instrument"], timeframe=meta["timeframe"], source=meta["source"],
                start_ts=to_db_time(meta["start_ts"]) if meta.get("start_ts") is not None else None,
                end_ts=to_db_time(meta["end_ts"]) if meta.get("end_ts") is not None else None,
                n_rows=meta.get("n_rows"), quality_status=meta.get("quality_status"),
                quality_report=meta.get("quality_report")))
    except Exception:
        pass


# ---- engine state (key/value) ---------------------------------------------------------------------------------------
def get_state(key: str, default=None):
    with get_session() as s:
        row = s.get(EngineState, key)
        return default if row is None else row.value


def set_state(key: str, value) -> None:
    with get_session() as s:
        row = s.get(EngineState, key)
        if row is None:
            s.add(EngineState(key=key, value=value))
        else:
            row.value = value
