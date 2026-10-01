"""P0 scaffold checks: package imports, required docs exist, secrets are gitignored."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SUBPACKAGES = [
    "config", "utils", "brokers", "data", "data_adapters", "features", "regimes", "strategies",
    "backtesting", "analogues", "ranking", "research", "market_state", "risk", "execution",
    "database", "reports", "ui",
]


def test_package_has_version() -> None:
    pkg = importlib.import_module("delta_intelligence")
    assert isinstance(pkg.__version__, str)


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"delta_intelligence.{name}")


@pytest.mark.parametrize("doc", ["docs/REFERENCE_README.md", "docs/DELTA_API_NOTES.md", "CLAUDE.md"])
def test_required_docs_exist(doc: str) -> None:
    assert (ROOT / doc).stat().st_size > 1000


def test_secrets_are_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert ".streamlit/secrets.toml" in ignored


def test_env_example_defaults_are_safe() -> None:
    from dotenv import dotenv_values

    env = dotenv_values(ROOT / ".env.example")
    assert env["TRADING_MODE"] == "PAPER"
    assert env["DELTA_ENV"] == "TESTNET"
    assert env["TRADING_LIVE_CONFIRM"] == ""
    assert env["DELTA_API_KEY"] == "" and env["DELTA_API_SECRET"] == ""
