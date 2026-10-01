"""
Delta Exchange connectivity check (read-only): time sync, public data, auth, IP whitelist, account reads.

    python scripts/check_delta.py              # uses DELTA_ENV (default TESTNET) and keys from .env
    python scripts/check_delta.py --no-keys    # public checks only

Exit code 0 when every critical check passes. It never prints keys, secrets or signatures, and never places orders.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delta_intelligence.brokers.connectivity import all_critical_passed, run_connectivity_check  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-keys", action="store_true", help="only run the public checks")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Delta connectivity check - environment {settings.delta_env}\n")
    results = run_connectivity_check(settings, require_credentials=not args.no_keys)
    for r in results:
        mark = "WARN" if r.warning else ("PASS" if r.ok else "FAIL")
        print(f"[{mark}] {r.name}: {r.detail}")
    ok = all_critical_passed(results)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
