"""
Headless UI smoke test: executes app.py and EVERY page with Streamlit's AppTest and reports exceptions.

    python scripts/ui_smoke_test.py            # offline: no network, temporary database
    python scripts/ui_smoke_test.py --online   # real Delta public data, a COPY of the database, and it clicks the
                                               # Backtest Lab / Analogues buttons

Offline mode (UI_OFFLINE=1) makes no Delta calls and reads cached candles only; pages fall back to "no data" messages
where nothing is cached. Exit code 0 when every page renders without an exception.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(timeout: float = 300.0, online: bool = False) -> list[tuple[str, bool, str, float]]:
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="di_ui_"))
    if online:
        os.environ.pop("UI_OFFLINE", None)
        real = ROOT / "data_cache" / "delta_intelligence.db"
        if real.exists():
            shutil.copy(real, tmp / "ui.db")  # never touch the live engine's database
    else:
        os.environ["UI_OFFLINE"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'ui.db').as_posix()}"
    os.environ["APP_PASSWORD"] = ""
    os.chdir(ROOT)
    from streamlit.testing.v1 import AppTest

    results = []
    targets = [ROOT / "app.py"] + sorted((ROOT / "app_pages").glob("*.py"))
    for path in targets:
        t0 = time.time()
        at = AppTest.from_file(str(path), default_timeout=timeout)
        try:
            at.run()
            if online and path.name in ("05_backtest_lab.py", "07_historical_analogues.py") and not at.exception:
                if path.name == "05_backtest_lab.py":
                    at.slider[0].set_value(30)
                at.button[0].click().run()
            errs = [e.value for e in at.exception]
            ok = not errs
            msg = "" if ok else str(errs[0])[:300]
        except Exception as exc:  # timeouts, import errors
            ok, msg = False, f"{type(exc).__name__}: {exc}"[:300]
        results.append((path.name, ok, msg, time.time() - t0))
    return results


def main() -> int:
    res = run(online="--online" in sys.argv)
    for name, ok, msg, secs in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:32} {secs:5.1f}s {msg}")
    bad = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(bad)}/{len(res)} pages rendered without exceptions")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
