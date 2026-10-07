"""Test setup for the sandbox: offline, throwaway caches, project and lab importable. Run with
`python -m pytest research_lab/tests` from the repository root (the main `python -m pytest` is unaffected)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "research_lab")):
    if p not in sys.path:
        sys.path.insert(0, p)

_TMP = Path(tempfile.mkdtemp(prefix="research_lab_tests_"))
os.environ["LOGS_DIR"] = str(_TMP / "logs")
os.environ["DATA_CACHE_DIR"] = str(_TMP / "data_cache")
for _k in ("DELTA_API_KEY", "DELTA_API_SECRET", "TRADING_MODE", "TRADING_LIVE_CONFIRM", "DELTA_ENV", "WATCHLIST"):
    os.environ.pop(_k, None)

import pytest  # noqa: E402

from delta_intelligence.config import settings as settings_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    settings_mod.set_settings(settings_mod.Settings.from_env({
        "DATA_CACHE_DIR": str(tmp_path / "data_cache"), "LOGS_DIR": str(_TMP / "logs")}))
    yield
    settings_mod.set_settings(None)
