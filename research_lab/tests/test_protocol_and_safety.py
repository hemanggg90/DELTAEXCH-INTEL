"""The frozen protocol cannot change quietly, and the sandbox cannot trade or touch the main project."""
from __future__ import annotations

import re
from pathlib import Path

from lab.library import BUCKETS, VARIANTS
from lab.protocol import protocol_dict, protocol_hash

LAB = Path(__file__).resolve().parents[1]
ROOT = LAB.parent
FROZEN = LAB / "FROZEN_PROTOCOL_HASH.txt"


def test_protocol_matches_the_frozen_hash() -> None:
    """After the methodology freeze, ANY change to a variant, parameter, bucket, split date or gate fails this test.
    Re-freezing is a deliberate act (and must be disclosed in the report)."""
    assert FROZEN.exists(), "protocol has not been frozen yet"
    assert protocol_hash() == FROZEN.read_text().strip()


def test_protocol_describes_the_whole_experiment() -> None:
    d = protocol_dict()
    assert len(d["variants"]) == len(VARIANTS) and set(d["buckets"]) == set(BUCKETS)
    assert d["gates"]["min_trades"] == 200 and d["gates"]["min_assets"] == 2 and d["perturbation"] == [0.8, 1.2]
    assert d["timeframes"] == ["5m", "15m"] and d["assets"] == ["BTC", "ETH", "XAUT"]
    assert len({v.id for v in VARIANTS}) == len(VARIANTS)  # ids are unique


def test_every_strategy_has_a_small_prespecified_variant_set() -> None:
    per: dict = {}
    for v in VARIANTS:
        per.setdefault(v.strategy_name, []).append(v.id)
    assert len(per) == 10
    assert all(2 <= len(ids) <= 3 for ids in per.values())  # hypothesis-driven, no grid


def test_lab_code_contains_no_order_or_withdrawal_or_broker_code() -> None:
    forbidden = ["place" + "_order(", "allow_orders" + "=True", "with" + "draw", "/v2/" + "heartbeat", "close" + "_all",
                 "orders/" + "all", "delta_intelligence.brokers", "DeltaBroker", "PaperBroker", "requests.post"]
    offenders = []
    for p in (LAB / "lab").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        offenders += [(p.name, f) for f in forbidden if f in text]
    run = (LAB / "run_research.py").read_text(encoding="utf-8")
    offenders += [("run_research.py", f) for f in forbidden if f in run]
    assert not offenders, offenders


def test_lab_never_uses_synthetic_data_outside_tests() -> None:
    for p in (LAB / "lab").rglob("*.py"):
        assert "synthetic" not in p.read_text(encoding="utf-8"), p.name
    assert (LAB / "run_research.py").read_text(encoding="utf-8").count("synthetic") == 0


def test_lab_only_imports_the_project_read_only_modules() -> None:
    """The lab may import the project's research code; it must not import execution/broker/risk state writers."""
    banned = re.compile(r"from delta_intelligence\.(brokers|database|risk\.risk_engine\b.*import (set_|record)|"
                        r"execution\.(engine|recovery|account))")
    for p in (LAB / "lab").rglob("*.py"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith(("from ", "import ")):
                assert not banned.search(line), (p.name, line)


def test_the_sandbox_is_outside_the_main_package_and_main_pytest_root() -> None:
    assert not (ROOT / "delta_intelligence" / "research_lab").exists()
    ini = (ROOT / "pytest.ini").read_text()
    assert "testpaths = tests" in ini  # the main suite never collects research_lab
