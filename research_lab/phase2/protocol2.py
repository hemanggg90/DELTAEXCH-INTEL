"""Phase 2 frozen protocol hash: policies, thresholds and the source of the files that implement Phase 2, plus the
Phase 1 hash (the strategies must still be exactly the Phase 1 strategies)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from phase2 import analysis as A
from phase2.policies import POLICIES, policy_dict

VERSION = "2.0"
FROZEN_FILES = ("policies.py", "analysis.py", "runner2.py", "pipeline2.py")
HERE = Path(__file__).resolve().parent
PHASE1_FROZEN = HERE.parent / "FROZEN_PROTOCOL_HASH.txt"


def protocol2_dict() -> dict:
    return {"version": VERSION, "phase1_hash": PHASE1_FROZEN.read_text().strip(),
            "policies": [policy_dict(p) for p in POLICIES],
            "real_data_thresholds": A.DEFAULT_REAL.__dict__, "min_discovery_trades": A.MIN_DISCOVERY_TRADES,
            "min_policy_breadth": A.MIN_POLICY_BREADTH,
            "sources": {n: hashlib.sha256((HERE / n).read_text(encoding="utf-8").encode()).hexdigest() for n in FROZEN_FILES}}


def protocol2_hash() -> str:
    return hashlib.sha256(json.dumps(protocol2_dict(), sort_keys=True, default=str).encode()).hexdigest()
