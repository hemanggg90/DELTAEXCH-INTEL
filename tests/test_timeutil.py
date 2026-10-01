from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from delta_intelligence.utils import timeutil as tu

UTC = dt.timezone.utc


def u(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        tu.ensure_utc(dt.datetime(2026, 10, 1, 12, 0))


def test_ist_display_is_plus_0530() -> None:
    assert tu.fmt_ist(u(2026, 10, 1, 0, 0), "%H:%M") == "05:30"
    assert tu.to_ist(u(2026, 10, 1, 18, 30)).date() == dt.date(2026, 10, 2)


def test_exchange_day_boundary_is_0000_utc_0530_ist() -> None:
    assert tu.exchange_day(u(2026, 9, 30, 23, 59)) == dt.date(2026, 9, 30)  # 05:29 IST on 1 Oct
    assert tu.exchange_day(u(2026, 10, 1, 0, 0)) == dt.date(2026, 10, 1)  # 05:30 IST
    s = pd.Series(pd.to_datetime(["2026-09-30T23:55Z", "2026-10-01T00:00Z"]))
    assert list(tu.exchange_day_series(s)) == [dt.date(2026, 9, 30), dt.date(2026, 10, 1)]


def test_risk_day_boundary_is_midnight_ist() -> None:
    assert tu.risk_day(u(2026, 10, 1, 18, 29)) == dt.date(2026, 10, 1)  # 23:59 IST
    assert tu.risk_day(u(2026, 10, 1, 18, 30)) == dt.date(2026, 10, 2)  # 00:00 IST
    assert tu.risk_day_start_utc(u(2026, 10, 1, 20, 0)) == u(2026, 10, 1, 18, 30)


def test_risk_day_custom_start() -> None:
    assert tu.risk_day(u(2026, 10, 1, 23, 0), start="05:30") == dt.date(2026, 10, 1)  # 04:30 IST on 2 Oct


@pytest.mark.parametrize("utc_time,expected", [
    (u(2026, 10, 1, 1, 0), ["ASIA"]),  # 06:30 IST
    (u(2026, 10, 1, 14, 30), ["EUROPE", "US"]),  # 20:00 IST overlap
    (u(2026, 10, 1, 19, 30), ["US"]),  # 01:00 IST: US crosses midnight
    (u(2026, 10, 1, 21, 30), []),  # 03:00 IST
])
def test_liquidity_sessions(utc_time: dt.datetime, expected: list[str]) -> None:
    sessions = tu.parse_sessions("ASIA=05:30-13:30,EUROPE=13:30-21:30,US=19:00-02:00")
    assert tu.liquidity_sessions_at(utc_time, sessions) == expected


def test_bad_session_spec_raises() -> None:
    with pytest.raises(ValueError):
        tu.parse_sessions("ASIA=0530-1330")


def test_next_funding_time() -> None:
    assert tu.next_funding_time(u(2026, 10, 1, 7, 59)) == u(2026, 10, 1, 8, 0)
    assert tu.next_funding_time(u(2026, 10, 1, 8, 0)) == u(2026, 10, 1, 16, 0)  # strictly after
    assert tu.next_funding_time(u(2026, 10, 1, 23, 0)) == u(2026, 10, 2, 0, 0)
    assert tu.fmt_ist(tu.next_funding_time(u(2026, 10, 1, 1, 0)), "%H:%M") == "13:30"


def test_funding_times_between() -> None:
    assert tu.funding_times_between(u(2026, 10, 1, 7, 0), u(2026, 10, 2, 0, 0)) == [
        u(2026, 10, 1, 8), u(2026, 10, 1, 16), u(2026, 10, 2, 0)]
    assert tu.funding_times_between(u(2026, 10, 1, 8, 0), u(2026, 10, 1, 9, 0)) == []


def test_bar_flooring_and_last_closed() -> None:
    assert tu.floor_to_timeframe(u(2026, 10, 1, 10, 7, 30), "5m") == u(2026, 10, 1, 10, 5)
    assert tu.last_closed_bar_start(u(2026, 10, 1, 10, 7), "5m") == u(2026, 10, 1, 10, 0)
    assert tu.last_closed_bar_start(u(2026, 10, 1, 10, 5), "5m") == u(2026, 10, 1, 10, 0)
    assert tu.last_closed_bar_start(u(2026, 10, 1, 10, 7), "1d") == u(2026, 9, 30)


def test_unknown_timeframe_raises() -> None:
    with pytest.raises(ValueError):
        tu.timeframe_seconds("7m")


def test_epoch_helpers() -> None:
    assert tu.from_epoch_us(1790878882511315).year == 2026
    assert tu.from_epoch(0) == u(1970, 1, 1)
