"""Static guards on the codebase itself."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "delta_intelligence"


def _sources():
    return [(p, p.read_text(encoding="utf-8")) for p in PKG.rglob("*.py")]


def test_synthetic_data_never_imported_at_runtime() -> None:
    offenders = [p.name for p, src in _sources()
                 if p.name != "synthetic.py" and re.search(r"import .*synthetic|from .*synthetic", src)]
    assert offenders == []


def test_no_withdrawal_functionality() -> None:
    pattern = re.compile(r"withdraw|/v2/wallets?/.*transfer|sub_account_balance_transfer", re.IGNORECASE)
    offenders = [p.name for p, src in _sources()
                 if any(pattern.search(line) and not line.lstrip().startswith("#") for line in src.splitlines())]
    assert offenders == []


def test_no_hardcoded_product_ids_in_watchlist() -> None:
    src = (PKG / "config" / "watchlist.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(27|3136|14823|14969)\b", src)
