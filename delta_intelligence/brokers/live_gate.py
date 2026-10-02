"""
LIVE startup gate: the only way the app obtains a `DeltaBroker`.

`build_live_broker` refuses unless
1. TRADING_MODE=LIVE and TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK (the broker re-checks both),
2. API credentials exist for DELTA_ENV,
3. every CRITICAL connectivity check passes (reachability, time sync, products, authentication + IP whitelist,
   positions and open-orders reads),
4. the chain venue is the trading venue (LIVE forces `data_env == delta_env`).

The check is read-only. Engine start then runs `DeltaBroker.reconcile()` before any new trade (execution/recovery).
"""
from __future__ import annotations

from collections.abc import Callable

from delta_intelligence.brokers.connectivity import CheckResult, all_critical_passed, run_connectivity_check
from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.brokers.delta_broker import DeltaBroker, LiveNotAuthorizedError
from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, Settings, get_settings
from delta_intelligence.options.chain import OptionChain


class LiveGateError(RuntimeError):
    """A startup requirement failed. `results` holds the connectivity checks, when they ran."""

    def __init__(self, message: str, results: list[CheckResult] | None = None):
        super().__init__(message)
        self.results = results or []


def gate_requirements(s: Settings) -> list[tuple[str, bool, str]]:
    """(requirement, met, detail) for the Live page and for error messages. No network."""
    return [
        ("TRADING_MODE=LIVE", s.is_live_mode, f"now {s.trading_mode}"),
        (f"TRADING_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE}", s.trading_live_confirm == LIVE_CONFIRM_PHRASE,
         "set" if s.trading_live_confirm == LIVE_CONFIRM_PHRASE else "not set"),
        ("API key + secret", s.has_credentials, "set" if s.has_credentials else "not set"),
        ("Trading environment", True, f"{s.delta_env} ({s.rest_base_url})"),
        ("Market data from the trading venue", s.data_env == s.delta_env, f"data {s.data_env}"),
    ]


def build_live_broker(chain: Callable[[], OptionChain], settings: Settings | None = None,
                      client: DeltaClient | None = None) -> DeltaBroker:
    s = settings or get_settings()
    unmet = [f"{n} ({d})" for n, ok, d in gate_requirements(s) if not ok]
    if unmet:
        raise LiveGateError("LIVE start refused: " + "; ".join(unmet))
    probe = client or DeltaClient(s.rest_base_url, s.delta_api_key, s.delta_api_secret, environment=s.delta_env,
                                  limits_settings=s, allow_orders=True)
    results = run_connectivity_check(s, probe)
    if not all_critical_passed(results):
        failed = "; ".join(f"{r.name}: {r.detail}" for r in results if r.critical and not r.ok)
        raise LiveGateError(f"LIVE start refused, connectivity check failed: {failed}", results)
    try:
        return DeltaBroker(chain, s, client=probe)
    except LiveNotAuthorizedError as exc:
        raise LiveGateError(str(exc), results) from None
