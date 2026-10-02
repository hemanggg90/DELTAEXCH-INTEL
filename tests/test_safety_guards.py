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


def test_only_the_live_broker_and_gate_can_send_orders() -> None:
    """`place_order(` is called, and `allow_orders=True` is set, only inside the live broker and the startup gate."""
    allowed = {"delta_broker.py", "live_gate.py", "delta_api_client.py"}
    calls = [p.name for p, src in _sources() if re.search(r"\.place_order\(", src) and p.name not in allowed]
    flags = [p.name for p, src in _sources() if re.search(r"allow_orders\s*=\s*True", src) and p.name not in allowed]
    assert calls == [] and flags == []


def test_live_broker_runs_the_order_guard_before_any_request() -> None:
    src = (PKG / "brokers" / "delta_broker.py").read_text(encoding="utf-8")
    send = src[src.index("def _send_leg"):src.index("def _update_order")]
    assert send.index("check_order(") < send.index("place_order(")
    assert '"reduce_only": not buy' in send and '"time_in_force": "ioc"' in send


def test_no_dead_mans_switch_or_cancel_all_in_code() -> None:
    pattern = re.compile(r"/v2/heartbeat|orders/all|close_all", re.IGNORECASE)
    offenders = [p.name for p, src in _sources()
                 if any(pattern.search(line) and not line.lstrip().startswith(("#", '"""')) for line in src.splitlines())]
    assert offenders == []
