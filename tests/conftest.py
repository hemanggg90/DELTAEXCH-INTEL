"""Shared test fixtures. Tests never touch the network or the real data_cache/ and logs/ folders."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Point logs and caches at a throwaway folder BEFORE any project module builds settings or the logger.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="delta_intel_tests_"))
os.environ["LOGS_DIR"] = str(_TMP_ROOT / "logs")
os.environ["DATA_CACHE_DIR"] = str(_TMP_ROOT / "data_cache")
for _k in ("DELTA_API_KEY", "DELTA_API_SECRET", "TRADING_MODE", "TRADING_LIVE_CONFIRM", "DELTA_ENV", "WATCHLIST"):
    os.environ.pop(_k, None)

import pytest  # noqa: E402
import responses  # noqa: E402

from delta_intelligence.brokers.cache import clear_delta_caches  # noqa: E402
from delta_intelligence.brokers.rate_limit import LIMITER  # noqa: E402
from delta_intelligence.config import settings as settings_mod  # noqa: E402
from delta_intelligence.data import data_manager as dm_mod  # noqa: E402

LOGS_DIR = _TMP_ROOT / "logs"


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    settings_mod.set_settings(settings_mod.Settings.from_env({
        "DATA_CACHE_DIR": str(tmp_path / "data_cache"), "LOGS_DIR": str(LOGS_DIR)}))
    LIMITER.reset()
    clear_delta_caches()
    dm_mod._last_attempt.clear()
    yield
    settings_mod.set_settings(None)


@pytest.fixture
def mocked():
    """`responses` mock that fails any request not explicitly registered (no accidental network)."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


class FakeClock:
    """Deterministic clock + sleep: sleeping advances time instead of blocking."""

    def __init__(self, start: float = 1_790_000_000.0):
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
