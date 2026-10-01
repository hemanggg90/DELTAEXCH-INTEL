"""
Time handling. UTC inside; IST on screen.

Every timestamp the system stores or computes with is a timezone-aware UTC datetime or pandas Timestamp. IST
(Asia/Kolkata, UTC+05:30) is used only for display, and for the risk day where the user configures it.

There are three independent clocks:

1. **Exchange day.** Starts at 00:00 UTC (05:30 IST) by default, matching Delta's 1d candles. Used for daily
   candles, pivots/CPR and the VWAP reset. See `exchange_day`.
2. **Risk day.** Starts at 00:00 IST by default. Used for the daily loss limit, the trade counter and reports. See
   `risk_day` and `risk_day_start_utc`.
3. **Liquidity sessions.** Asia, Europe and US windows in IST; windows may overlap or cross midnight. See
   `liquidity_sessions_at`.

The market is 24x7: there is no session close and no holidays.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

UTC = dt.timezone.utc
IST = ZoneInfo("Asia/Kolkata")

# Delta funding: every 8 h at 00:00/08:00/16:00 UTC (verified from the FUNDING series, docs section 7).
FUNDING_INTERVAL = dt.timedelta(hours=8)

# Resolutions accepted by /v2/history/candles (verified from the API's own validation error, docs section 6).
RESOLUTION_SECONDS: dict[str, int] = {
    "5s": 5, "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400, "1w": 604800,
}


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def ensure_utc(moment: dt.datetime | pd.Timestamp) -> dt.datetime:
    """Return an aware UTC datetime. Naive input is REJECTED: guessing a timezone is how 5.5 h bugs happen."""
    if isinstance(moment, pd.Timestamp):
        moment = moment.to_pydatetime()
    if moment.tzinfo is None:
        raise ValueError(f"naive datetime {moment!r}: pass a timezone-aware value (UTC internally)")
    return moment.astimezone(UTC)


def to_ist(moment: dt.datetime | pd.Timestamp) -> dt.datetime:
    return ensure_utc(moment).astimezone(IST)


def fmt_ist(moment: dt.datetime | pd.Timestamp | None, fmt: str = "%d %b %H:%M:%S IST") -> str:
    return "-" if moment is None else to_ist(moment).strftime(fmt)


def from_epoch(seconds: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(seconds, UTC)


def from_epoch_us(micros: int | float | str) -> dt.datetime:
    """Delta reports most timestamps in microseconds."""
    return dt.datetime.fromtimestamp(int(micros) / 1_000_000, UTC)


# ---- timeframes ------------------------------------------------------------------------------------------------
def timeframe_seconds(timeframe: str) -> int:
    try:
        return RESOLUTION_SECONDS[timeframe]
    except KeyError:
        raise ValueError(f"unsupported timeframe {timeframe!r}; Delta accepts {', '.join(RESOLUTION_SECONDS)}") from None


def floor_to_timeframe(moment: dt.datetime, timeframe: str) -> dt.datetime:
    """Start of the bar containing `moment`. Bars are aligned to the Unix epoch, as Delta's are. Weekly bars are
    UNVERIFIED for alignment and are not used by the system."""
    step = timeframe_seconds(timeframe)
    ts = int(ensure_utc(moment).timestamp())
    return from_epoch(ts - ts % step)


def last_closed_bar_start(now: dt.datetime, timeframe: str) -> dt.datetime:
    """Open time of the most recent bar that has fully closed at `now`."""
    return floor_to_timeframe(now, timeframe) - dt.timedelta(seconds=timeframe_seconds(timeframe))


# ---- clock 1: exchange day -------------------------------------------------------------------------------------
def exchange_day(moment: dt.datetime | pd.Timestamp, offset_min: int = 0) -> dt.date:
    """Exchange-day date for `moment` (the day starts at 00:00 UTC + offset_min)."""
    return (ensure_utc(moment) - dt.timedelta(minutes=offset_min)).date()


def exchange_day_series(timestamps: pd.Series, offset_min: int = 0) -> pd.Series:
    """Vectorised `exchange_day` for a tz-aware UTC series (used by features: VWAP reset, pivots)."""
    ts = pd.to_datetime(timestamps)
    if ts.dt.tz is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return (ts.dt.tz_convert(UTC) - pd.Timedelta(minutes=offset_min)).dt.date


# ---- clock 2: risk day -----------------------------------------------------------------------------------------
def _parse_hhmm(text: str) -> dt.time:
    hh, mm = text.strip().split(":")
    return dt.time(int(hh), int(mm))


def risk_day(moment: dt.datetime, tz: str = "Asia/Kolkata", start: str = "00:00") -> dt.date:
    """Risk-day date: the local date in `tz`, shifted so the day begins at `start`."""
    local = ensure_utc(moment).astimezone(ZoneInfo(tz))
    t = _parse_hhmm(start)
    return (local - dt.timedelta(hours=t.hour, minutes=t.minute)).date()


def risk_day_start_utc(moment: dt.datetime, tz: str = "Asia/Kolkata", start: str = "00:00") -> dt.datetime:
    """UTC instant at which the risk day containing `moment` began."""
    day = risk_day(moment, tz, start)
    local_start = dt.datetime.combine(day, _parse_hhmm(start), tzinfo=ZoneInfo(tz))
    return local_start.astimezone(UTC)


# ---- clock 3: liquidity sessions -------------------------------------------------------------------------------
@dataclass(frozen=True)
class LiquiditySession:
    name: str
    start: dt.time  # IST
    end: dt.time  # IST; may be earlier than start (crosses midnight)

    def contains(self, moment: dt.datetime) -> bool:
        t = to_ist(moment).time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


_SESSION_RE = re.compile(r"^\s*([A-Za-z_]+)\s*=\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$")


def parse_sessions(spec: str) -> tuple[LiquiditySession, ...]:
    """Parse 'ASIA=05:30-13:30,EUROPE=13:30-21:30,US=19:00-02:00' (IST)."""
    sessions = []
    for part in spec.split(","):
        if not part.strip():
            continue
        m = _SESSION_RE.match(part)
        if not m:
            raise ValueError(f"bad liquidity session spec {part!r}; expected NAME=HH:MM-HH:MM")
        sessions.append(LiquiditySession(m.group(1).upper(), _parse_hhmm(m.group(2)), _parse_hhmm(m.group(3))))
    return tuple(sessions)


def liquidity_sessions_at(moment: dt.datetime, sessions: tuple[LiquiditySession, ...]) -> list[str]:
    """Names of all sessions active at `moment` (Europe and US overlap 19:00-21:30 IST by default)."""
    return [s.name for s in sessions if s.contains(moment)]


# ---- funding -----------------------------------------------------------------------------------------------------
def next_funding_time(now: dt.datetime) -> dt.datetime:
    """Next 00:00/08:00/16:00 UTC funding instant strictly after `now`. Prefer the WebSocket `nfr` field when
    available; this is the schedule observed on 2026-10-01."""
    now = ensure_utc(now)
    step = int(FUNDING_INTERVAL.total_seconds())
    ts = int(now.timestamp())
    return from_epoch(ts - ts % step + step)


def funding_times_between(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Funding instants t with start < t <= end (a position open over [start, end] pays/receives at each)."""
    out = []
    t = next_funding_time(start)
    end = ensure_utc(end)
    while t <= end:
        out.append(t)
        t += FUNDING_INTERVAL
    return out
