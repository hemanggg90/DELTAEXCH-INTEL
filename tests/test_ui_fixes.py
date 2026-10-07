"""Summary tiles must not be squeezed into truncated one-row layouts, and the 'engine in another process' notice must say which
engine it is. Offline."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from delta_intelligence.database import db
from delta_intelligence.execution.engine import OWNER_KEY, TradingEngine, code_version, other_engine_alive
from delta_intelligence.ui.components import KPI_MAX_PER_ROW, kpi_layout
from delta_intelligence.ui.ranker_view import external_engine_message

_spec = importlib.util.spec_from_file_location("engine_test_helpers", Path(__file__).with_name("test_engine.py"))
_h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_h)
NOW, FakeDM, flip_frame, env = _h.NOW, _h.FakeDM, _h.flip_frame, _h.env


# ---- tiles ---------------------------------------------------------------------------------------------------------------------------
def test_tiles_are_split_into_rows_of_at_most_four_with_equal_widths() -> None:
    assert kpi_layout(0) == [] and kpi_layout(1) == [(0, 1)] and kpi_layout(4) == [(0, 4)]
    assert kpi_layout(7) == [(0, 4), (4, 4)]  # the Command Center's seven tiles: 4 + 3, each as wide as a 4-tile row
    assert kpi_layout(5) == [(0, 3), (3, 3)] and kpi_layout(8) == [(0, 4), (4, 4)]
    for n in range(1, 25):
        rows = kpi_layout(n)
        assert all(width <= KPI_MAX_PER_ROW for _, width in rows)
        covered = [i for start, width in rows for i in range(start, min(start + width, n))]
        assert covered == list(range(n))  # every tile appears exactly once, in order


def test_kpi_row_renders_all_tiles_in_two_rows() -> None:
    from streamlit.testing.v1 import AppTest

    def app():
        from delta_intelligence.ui.components import kpi_row

        kpi_row([{"label": f"Tile {i}", "value": f"${i},000.00"} for i in range(7)])

    at = AppTest.from_function(app).run()
    assert not at.exception
    assert len(at.metric) == 7 and [m.label for m in at.metric] == [f"Tile {i}" for i in range(7)]
    assert len(at.columns) == 8  # two st.columns(4) rows

    def small():
        from delta_intelligence.ui.components import kpi_row

        kpi_row([{"label": "A", "value": "1"}, {"label": "B", "value": "2"}, {"label": "C", "value": "3"}])

    at = AppTest.from_function(small).run()
    assert len(at.metric) == 3 and len(at.columns) == 3  # a short row is unchanged


def test_the_css_lets_metric_labels_wrap_instead_of_truncating() -> None:
    from delta_intelligence.ui.theme import CSS

    assert "stMetricLabel" in CSS and "white-space:normal" in CSS and "text-overflow:clip" in CSS
    assert "clamp(" in CSS  # the value font scales with the width


# ---- the notice -----------------------------------------------------------------------------------------------------------------------
def test_notice_with_no_details_still_explains_the_situation() -> None:
    tone, text = external_engine_message({}, 17, "select", "abc1234")
    assert tone == "info" and "another process" in text and "17s" in text and "double-trade" in text and "kill switch" in text


def test_notice_names_the_engine_and_its_settings() -> None:
    owner = {"id": "MYPC:4242", "started_at": "2026-10-07T04:30:00+00:00", "mode": "PAPER", "ranker_mode": "select",
             "version": "abc1234"}
    tone, text = external_engine_message(owner, 17, "select", "abc1234")
    assert tone == "info" and "`MYPC:4242`" in text and "mode PAPER" in text and "ranker `select`" in text
    assert "code `abc1234`" in text and "10:00 IST" in text and "Ctrl+C" in text  # 04:30 UTC is 10:00 IST


def test_notice_warns_about_an_older_engine_without_the_ranker() -> None:
    old = {"id": "MYPC:1", "started_at": "2026-10-06T04:30:00"}  # written before these fields existed
    tone, text = external_engine_message(old, 17, "select", "abc1234")
    assert tone == "warning" and "OLDER engine" in text and "does not use the ranker leaderboard" in text


def test_notice_warns_when_the_other_engine_is_not_using_the_ranker_or_runs_other_code() -> None:
    shadow = {"id": "H:2", "started_at": "2026-10-07T04:30:00", "mode": "PAPER", "ranker_mode": "off", "version": "abc1234"}
    tone, text = external_engine_message(shadow, 5, "select", "abc1234")
    assert tone == "warning" and "NOT letting the ranker choose trades" in text
    other_code = {**shadow, "ranker_mode": "select", "version": "zzz9999"}
    tone, text = external_engine_message(other_code, 5, "select", "abc1234")
    assert tone == "warning" and "different code" in text and "`zzz9999`" in text
    unknown = {**shadow, "ranker_mode": "select", "version": "unknown"}
    assert external_engine_message(unknown, 5, "select", "abc1234")[0] == "info"  # an unknown version is not a mismatch


# ---- the engine records who it is --------------------------------------------------------------------------------------------------------
def test_engine_start_records_mode_ranker_and_version(env) -> None:
    s, box, broker = env
    eng = TradingEngine(broker, lambda: box["chain"], FakeDM(), s, clock=lambda: NOW, interval_sec=30.0,
                        frame_builder=lambda perp, now: (flip_frame(), "OK"))
    eng.start()
    try:
        rec = db.get_state(OWNER_KEY)
    finally:
        eng.stop()
    assert rec["mode"] == "PAPER" and rec["ranker_mode"] == "off" and rec["ranker"] is None
    assert rec["version"] == code_version() and rec["id"] and rec["started_at"]


def test_old_owner_records_still_work_for_the_other_engine_check(env) -> None:
    s, box, broker = env
    db.set_state(OWNER_KEY, {"id": "OLDHOST:7", "started_at": NOW.isoformat()})
    db.set_state("engine_heartbeat", (NOW - dt.timedelta(seconds=10)).isoformat())
    other = other_engine_alive(NOW)
    assert other is not None and other["id"] == "OLDHOST:7" and "version" not in other  # parsed, with no new keys required
    assert external_engine_message(other, other["heartbeat_age_sec"], "select", "abc")[0] == "warning"


def test_code_version_is_a_short_hash_or_unknown() -> None:
    v = code_version()
    assert v == "unknown" or (len(v) == 7 and all(c in "0123456789abcdef" for c in v))
