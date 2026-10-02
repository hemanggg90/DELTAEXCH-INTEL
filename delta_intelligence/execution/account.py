"""
Account tracking for the risk engine: equity, peak equity, risk-day P&L and trades today. Durable in the
`engine_state` table, so it survives restarts.

The risk day starts at 00:00 IST (configurable via RISK_DAY_TZ / RISK_DAY_START). At rollover, the day-start equity
snapshot resets the daily loss counter.
"""
from __future__ import annotations

import datetime as dt

from delta_intelligence.config.settings import Settings
from delta_intelligence.database import db
from delta_intelligence.database.models import Position, to_db_time
from delta_intelligence.risk.risk_engine import AccountState
from delta_intelligence.utils.timeutil import risk_day, risk_day_start_utc

KEY = "account_tracker"


class AccountTracker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _state(self) -> dict:
        return db.get_state(KEY, {}) or {}

    def update(self, snapshot: dict, now: dt.datetime) -> dict:
        """Roll the risk day if needed, update the peak, persist. Returns the state."""
        c = self.settings.clocks
        today = str(risk_day(now, c.risk_day_tz, c.risk_day_start))
        st = self._state()
        eq = float(snapshot["equity"])
        if st.get("risk_day") != today:
            st["risk_day"] = today
            st["day_start_equity"] = eq
        st["peak_equity"] = max(float(st.get("peak_equity", eq)), eq)
        st["equity"] = eq
        db.set_state(KEY, st)
        return st

    def trades_today(self, now: dt.datetime, mode: str = "PAPER") -> int:
        c = self.settings.clocks
        start = to_db_time(risk_day_start_utc(now, c.risk_day_tz, c.risk_day_start))
        with db.get_session() as s:
            return s.query(Position).filter(Position.mode == mode, Position.opened_at >= start).count()

    def account_state(self, snapshot: dict, now: dt.datetime, broker_connected: bool = True,
                      kill_switch: bool = False) -> AccountState:
        st = self.update(snapshot, now)
        return AccountState(
            equity=snapshot["equity"], peak_equity=st["peak_equity"],
            daily_pnl=snapshot["equity"] - st["day_start_equity"], trades_today=self.trades_today(now),
            open_positions=snapshot["open_positions"], available_cash=snapshot["available_cash"],
            exposure_by_strategy=snapshot["exposure_by_strategy"], exposure_by_bucket=snapshot["exposure_by_bucket"],
            total_exposure=snapshot["total_exposure"], broker_connected=broker_connected, kill_switch=kill_switch)
