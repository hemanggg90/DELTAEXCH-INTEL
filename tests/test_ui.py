from __future__ import annotations

import pytest

from delta_intelligence.ui.format import indian_group, inr, money, pnl, usd


def test_indian_grouping() -> None:
    assert indian_group(1234567.891) == "12,34,567.89"
    assert indian_group(999) == "999.00"
    assert indian_group(100000, 0) == "1,00,000"
    assert indian_group(-12345678, 0) == "-1,23,45,678"


def test_money_formats() -> None:
    assert usd(1234.5) == "$1,234.50" and usd(-3) == "-$3.00" and usd(None) == "–"
    assert inr(100, 84.0) == "₹8,400" and inr(100, None) == "–"
    assert money(1000, 84.0) == "$1,000.00 (≈ ₹84,000)" and money(5, None) == "$5.00"


def test_pnl_never_relies_on_colour_alone() -> None:
    assert pnl(12.3).startswith("▲ +$") and pnl(-4.1).startswith("▼ -$") and pnl(0).startswith("■")


def test_new_formatters_and_missing_values() -> None:
    import datetime as dt

    from delta_intelligence.ui.format import arrow, duration, num, pct, r_mult, text, tone

    assert num(1234.567, 1) == "1,234.6" and money(12, None, signed=True) == "+$12.00" and usd(-2, signed=True) == "-$2.00"
    assert duration(dt.timedelta(minutes=45)) == "45m" and duration(dt.timedelta(hours=2, minutes=14)) == "2h 14m"
    assert duration(dt.timedelta(days=3, hours=4, minutes=9)) == "3d 4h" and duration(-90) == "-1m" and duration(42) == "42s"
    assert tone(3) == "good" and tone(-1) == "critical" and tone(0) == tone(None) == "neutral"
    assert arrow(1) == "▲" and arrow(-1) == "▼" and arrow(None) == "–"
    for f in (num, pct, r_mult, usd, pnl, text, duration):
        assert f(None) == "–"
    assert num(float("nan")) == "–" and pct(float("inf")) == "–" and indian_group(float("nan")) == "–"


def test_status_html_never_relies_on_colour_alone() -> None:
    from delta_intelligence.ui.components import chip, range_bar_html, value_html
    from delta_intelligence.ui.theme import TONE_ICON

    for tone_, icon in TONE_ICON.items():
        assert icon in chip("x", tone_)
    assert "▲ +$5.00" in value_html(5.0) and "▼ -$5.00" in value_html(-5.0) and "(+2.0%)" in value_html(5.0, 2.0)
    bar = range_bar_html(65, 100, 200, 80, labels=("stop", "entry", "2x"))
    assert "✖ stop" in bar and "2x ◎" in bar
    assert "&lt;script&gt;" in chip("<script>", "info")  # text is escaped


def test_pages_use_only_shared_helpers() -> None:
    import re
    from pathlib import Path

    pages = sorted((Path(__file__).resolve().parents[1] / "app_pages").glob("*.py"))
    assert pages
    for p in pages:
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{6}\b", src), f"{p.name}: hex colour"
        assert "<style" not in src and "unsafe_allow_html" not in src, f"{p.name}: raw CSS/HTML"
        assert "page_setup(" in src, f"{p.name}: must call page_setup first"


@pytest.mark.slow
def test_every_page_renders_offline() -> None:
    import importlib.util
    import os
    from pathlib import Path

    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    spec = importlib.util.spec_from_file_location("ui_smoke", Path(__file__).resolve().parents[1] / "scripts" / "ui_smoke_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        results = mod.run()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        os.chdir(saved_cwd)
    bad = [(n, m) for n, ok, m, _ in results if not ok]
    assert not bad, bad
    assert len(results) == 14


@pytest.mark.parametrize("password,mode,authed,expected", [
    ("", "PAPER", False, True),    # no password, paper: open
    ("", "LIVE", False, False),    # no password, live: read-only, never open
    ("pw", "PAPER", False, False),  # password set, not logged in
    ("pw", "PAPER", True, True),
    ("pw", "LIVE", True, True),
])
def test_controls_follow_password_and_mode(monkeypatch, password, mode, authed, expected) -> None:
    import streamlit as st

    from delta_intelligence.config.settings import Settings
    from delta_intelligence.ui import components

    s = Settings.from_env({"APP_PASSWORD": password, "TRADING_MODE": mode})
    monkeypatch.setattr(components, "get_settings", lambda: s)
    monkeypatch.setattr(st, "session_state", {"_authed": authed})
    assert components.can_control() is expected


def test_inr_pnl_and_default_rate() -> None:
    from delta_intelligence.config.settings import DEFAULT_USDINR_RATE, Settings
    from delta_intelligence.ui.format import pnl_inr

    assert pnl_inr(10, 84.0) == "▲ +₹840" and pnl_inr(-10, 84.0) == "▼ -₹840" and pnl_inr(0, 84.0) == "■ ₹0"
    assert pnl_inr(None, 84.0) == "–" and pnl_inr(5, None) == "–"
    assert Settings.from_env({}).usdinr_rate == DEFAULT_USDINR_RATE
    assert Settings.from_env({"USDINR_RATE": "83.5"}).usdinr_rate == 83.5


def test_amount_used_today_counts_only_the_risk_day(tmp_path) -> None:
    import datetime as dt

    from delta_intelligence.database import db
    from delta_intelligence.database.models import Position
    from delta_intelligence.ui.amounts import order_amount, used_today

    assert order_amount(500.0, 100, 0.001) == 50.0 and order_amount(None, 1, 0.001) is None
    assert order_amount(1.0, 1, None) is None
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    try:
        now = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.timezone.utc)  # 17:30 IST; risk day began 18:30 UTC on 2 Oct
        exp = now + dt.timedelta(days=1)

        def pos(pid, opened, max_loss, pnl=None, mode="PAPER"):
            return Position(position_id=pid, mode=mode, underlying="BTC", structure="LONG_CALL", expiry=exp,
                            status="CLOSED" if pnl is not None else "OPEN", opened_at=opened.replace(tzinfo=None),
                            entry_net_premium=max_loss - 1, max_loss=max_loss, realized_pnl=pnl)

        with db.get_session() as s:
            s.add_all([pos("a", now - dt.timedelta(hours=1), 51.0, -10.0),
                       pos("b", now - dt.timedelta(hours=2), 30.0),
                       pos("c", dt.datetime(2026, 10, 2, 18, 0), 99.0, 5.0),  # 23:30 IST on 2 Oct: yesterday
                       pos("d", now - dt.timedelta(hours=1), 70.0, mode="LIVE")])
        t = used_today(now, "PAPER")
        assert t == {"used": 81.0, "trades": 2, "realized": -10.0}
    finally:
        db.reset_engine()
