"""
Connectivity check: reachability, clock sync, public data, product resolution, auth, IP whitelist, account reads.

`scripts/check_delta.py` prints it. The LIVE startup gate (P7) requires every critical check to pass.

It is read-only: it never places, edits or cancels anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.brokers.errors import (
    AuthBlockedError,
    AuthError,
    DeltaError,
    IPNotWhitelistedError,
    PermissionDeniedError,
    SignatureExpiredError,
)
from delta_intelligence.config.settings import Settings
from delta_intelligence.config.watchlist import get_watchlist

CLOCK_WARN_SEC = 1.5
CLOCK_FAIL_SEC = 4.0  # the signature window is 5 s


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    critical: bool = True
    warning: bool = False


def _call(name: str, fn, critical: bool = True) -> tuple[CheckResult | None, object]:
    try:
        return None, fn()
    except IPNotWhitelistedError as exc:
        return CheckResult(name, False, str(exc), critical), None
    except SignatureExpiredError as exc:
        return CheckResult(name, False, f"{exc} (server_time={exc.server_time})", critical), None
    except PermissionDeniedError as exc:
        return CheckResult(name, False, str(exc), critical), None
    except (AuthBlockedError, AuthError) as exc:
        return CheckResult(name, False, str(exc), critical), None
    except DeltaError as exc:
        return CheckResult(name, False, f"{type(exc).__name__}: {exc}", critical), None
    except Exception as exc:  # unexpected: still report rather than crash the check
        return CheckResult(name, False, f"{type(exc).__name__}: {exc}", critical), None


def run_connectivity_check(settings: Settings, client: DeltaClient | None = None,
                           require_credentials: bool = True) -> list[CheckResult]:
    client = client or DeltaClient(settings.rest_base_url, settings.delta_api_key, settings.delta_api_secret,
                                   environment=settings.delta_env, limits_settings=settings)
    results: list[CheckResult] = [CheckResult(
        "configuration", True,
        f"DELTA_ENV={settings.delta_env}, REST={client.base_url}, TRADING_MODE={settings.trading_mode}, "
        f"credentials {'set' if settings.has_credentials else 'NOT set'}", critical=False)]

    # 1. Reachability + clock (public, weight 1). The Date header feeds client.clock_offset_sec.
    err, quota = _call("reachability", client.get_rate_limit_quota)
    if err:
        return results + [err]
    results.append(CheckResult("reachability", True, f"Delta answered; rate-limit quota now {quota}"))
    offset = client.last_measured_offset if client.last_measured_offset is not None else client.clock_offset_sec
    if abs(offset) >= CLOCK_FAIL_SEC:
        results.append(CheckResult("time sync", False, f"local clock is {offset:+.1f}s off Delta's; signatures "
                                   "expire after 5 s. Enable automatic time sync (NTP) on this machine."))
    elif abs(offset) >= CLOCK_WARN_SEC:
        results.append(CheckResult("time sync", True, f"local clock {offset:+.1f}s off; the client compensates, "
                                   "but enable NTP", warning=True))
    elif not client.clock_offset_measured:
        results.append(CheckResult("time sync", True, "Delta sent no server timestamp; could not measure the offset",
                                   warning=True))
    else:
        results.append(CheckResult("time sync", True, f"local clock within {CLOCK_WARN_SEC}s of Delta "
                                   f"(offset {offset:+.2f}s; corrected automatically when larger)"))

    # 2. Public market data on the trading environment + watchlist resolution.
    err, products = _call("products", client.get_products)
    if err:
        results.append(err)
    else:
        by_symbol = {p["symbol"]: p for p in products}
        missing = [s for s in get_watchlist() if s not in by_symbol]
        ids = ", ".join(f"{s}={by_symbol[s]['id']}" for s in get_watchlist() if s in by_symbol)
        results.append(CheckResult("watchlist products", not missing,
                                   f"resolved {ids}" + (f"; MISSING on {settings.delta_env}: {missing}" if missing else "")))
    err, ticker = _call("public ticker", lambda: client.get_ticker("BTCUSD", max_age=0))
    results.append(err or CheckResult("public ticker", True, f"BTCUSD mark {ticker.get('mark_price')}, "
                                      f"funding {ticker.get('funding_rate')}"))

    # 3. Authenticated reads: wallet needs a valid key, signature, clock AND (for Trading keys) a whitelisted IP.
    if not settings.has_credentials:
        results.append(CheckResult("authentication", not require_credentials,
                                   "DELTA_API_KEY / DELTA_API_SECRET not set (fine for PAPER; required for LIVE)",
                                   critical=require_credentials, warning=not require_credentials))
        return results
    err, balances = _call("authentication + IP whitelist", client.get_wallet_balances)
    if err:
        return results + [err]
    usd = next((b for b in balances if (b.get("asset_symbol") or "").upper() in ("USD", "USDT")), None)
    results.append(CheckResult("authentication + IP whitelist", True,
                               "signed request accepted from this IP"
                               + (f"; {usd.get('asset_symbol')} available {usd.get('available_balance')}" if usd else "")))
    err, positions = _call("positions read", client.get_positions)
    results.append(err or CheckResult("positions read", True, f"{len(positions)} open position(s)"))
    err, orders = _call("open orders read", client.get_open_orders)
    results.append(err or CheckResult("open orders read", True, f"{len(orders)} open order(s)"))
    return results


def all_critical_passed(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results if r.critical)
