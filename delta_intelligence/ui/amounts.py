"""Read-only 'amount used' figures for the dashboard (USD book; INR shown as an estimate by the caller).

Amount used by a position = premium paid + entry fees (incl. GST), i.e. its max loss for a long option. Amount of an
order = limit price x size x contract value; of a fill = price x size x contract value plus fee and GST."""
from __future__ import annotations

import datetime as dt

from delta_intelligence.database import db
from delta_intelligence.database.models import Position, to_db_time
from delta_intelligence.utils.timeutil import risk_day_start_utc


def position_amount_used(p: Position) -> float | None:
    """Premium paid + entry fees. Falls back to the premium alone when max_loss isn't stored."""
    if p.max_loss is not None:
        return float(p.max_loss)
    return None if p.entry_net_premium is None else float(p.entry_net_premium)


def order_amount(price: float | None, size: int | None, contract_value: float | None) -> float | None:
    if price is None or size is None or not contract_value:
        return None
    return float(price) * int(size) * float(contract_value)


def used_today(now: dt.datetime, mode: str, tz: str = "Asia/Kolkata", start: str = "00:00") -> dict:
    """Totals for positions opened since the risk-day start (00:00 IST): amount used, realised P&L of those closed so
    far, and the count."""
    since = to_db_time(risk_day_start_utc(now, tz, start))
    with db.get_session() as s:
        rows = s.query(Position).filter(Position.mode == mode, Position.opened_at >= since).all()
        used = [position_amount_used(p) for p in rows]
        realized = [p.realized_pnl for p in rows if p.realized_pnl is not None]
    return {"used": sum(u for u in used if u is not None), "trades": len(rows),
            "realized": sum(realized) if realized else None}
