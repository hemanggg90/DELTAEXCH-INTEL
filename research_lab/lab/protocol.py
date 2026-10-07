"""The frozen protocol and its hash.

`protocol_dict()` lists everything that defines the experiment: the variants and their parameters, the option buckets,
the split date and every gate threshold. `protocol_hash()` is its SHA-256. The hash is written into `PROTOCOL.md` when
the methodology is frozen, printed in every report, and checked by `tests/test_protocol.py`: changing any parameter
after the freeze fails that test, so methodology changes cannot be made quietly after seeing results.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lab import evaluate as E
from lab.library import BASELINE_BUCKET, BUCKETS, DTE_MULTIPLES, LEGACY, VARIANTS

PROTOCOL_VERSION = "1.0"
# The files that DEFINE the experiment. Their text (newlines normalised) is part of the hash, so even a "bug fix" to a
# strategy rule, an indicator, a gate or the control changes the hash and has to be re-frozen (and disclosed) on purpose.
FROZEN_FILES = ("strategies.py", "config.py", "indicators.py", "evaluate.py", "controls.py", "library.py", "frame.py",
                "base.py", "metrics.py", "runner.py")


def _source_hashes() -> dict:
    here = Path(__file__).resolve().parent
    return {n: hashlib.sha256((here / n).read_text(encoding="utf-8").encode()).hexdigest() for n in FROZEN_FILES}


def protocol_dict() -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "variants": [{"id": v.id, "strategy": v.strategy_name, "config_class": type(v.config).__name__,
                      "config": v.config.as_dict(), "key_params": list(v.config.key_params)} for v in VARIANTS],
        "legacy": [list(x) for x in LEGACY],
        "buckets": {k: {"moneyness": b.moneyness, "delta_min": b.delta_min, "delta_max": b.delta_max}
                    for k, b in BUCKETS.items()},
        "baseline_bucket": BASELINE_BUCKET, "dte_multiples": list(DTE_MULTIPLES),
        "gates": {"holdout_start": str(E.HOLDOUT_START), "min_trades": E.MIN_TRADES, "min_per_asset": E.MIN_PER_ASSET,
                  "min_assets": E.MIN_ASSETS, "min_holdout": E.MIN_HOLDOUT, "control_alpha": E.CONTROL_ALPHA,
                  "control_runs": E.CONTROL_RUNS, "n_folds": E.N_FOLDS, "min_buckets_positive": E.MIN_BUCKETS_POSITIVE},
        "perturbation": [0.8, 1.2],
        "timeframes": ["5m", "15m"],
        "assets": ["BTC", "ETH", "XAUT"],
        "sources": _source_hashes(),
    }


def protocol_hash() -> str:
    blob = json.dumps(protocol_dict(), sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()
