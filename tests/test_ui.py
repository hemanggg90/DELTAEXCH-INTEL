from __future__ import annotations

import pytest

from delta_intelligence.ui.format import indian_group, inr, money, pnl, usd


def test_indian_grouping() -> None:
    assert indian_group(1234567.891) == "12,34,567.89"
    assert indian_group(999) == "999.00"
    assert indian_group(100000, 0) == "1,00,000"
    assert indian_group(-12345678, 0) == "-1,23,45,678"


def test_money_formats() -> None:
    assert usd(1234.5) == "$1,234.50" and usd(-3) == "-$3.00" and usd(None) == "-"
    assert inr(100, 84.0) == "₹8,400" and inr(100, None) == "-"
    assert money(1000, 84.0) == "$1,000.00 (≈ ₹84,000)" and money(5, None) == "$5.00"


def test_pnl_never_relies_on_colour_alone() -> None:
    assert pnl(12.3).startswith("▲ +$") and pnl(-4.1).startswith("▼ -$") and pnl(0).startswith("■")


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
