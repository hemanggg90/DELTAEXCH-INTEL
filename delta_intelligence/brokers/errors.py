"""
Typed Delta Exchange errors, and the classifier that maps HTTP responses to them.

Response shapes were verified live on 2026-10-01 (docs/DELTA_API_NOTES.md section 2). `error` may be a string
(`"unauthorized"`) or an object (`{"code": "invalid_api_key", "context": {...}}`).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any


class DeltaError(RuntimeError):
    """Base class. `code` is Delta's error code when one was returned."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 payload: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


class DeltaConfigError(DeltaError):
    """Missing or invalid local configuration (e.g. no API key for an authenticated call)."""


class DeltaNetworkError(DeltaError):
    """Connection failure or timeout before a response arrived."""


class OrderStateUnknownError(DeltaNetworkError):
    """An order request failed in transit. The order MAY have been accepted. Never resend blindly: reconcile through
    the client_order_id."""


class DeltaServerError(DeltaError):
    """5xx from Delta."""


class AuthError(DeltaError):
    """The credentials were rejected: invalid key, signature mismatch, wrong environment, or not authorised."""


class SignatureExpiredError(AuthError):
    """The signature reached Delta more than 5 s after it was created (clock skew or latency)."""

    def __init__(self, message: str, *, server_time: int | None = None, request_time: int | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.server_time = server_time
        self.request_time = request_time


class PermissionDeniedError(AuthError):
    """The key lacks the permission this endpoint needs (Read Data or Trading)."""


class IPNotWhitelistedError(AuthError):
    """The calling IP is not on the key's whitelist. `ip` is the address Delta saw, when it reported one."""

    def __init__(self, message: str, *, ip: str | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.ip = ip


class CDNForbiddenError(DeltaError):
    """Blocked by Delta's CDN before reaching the API (e.g. missing User-Agent, or a hidden/blocked IP)."""


class RateLimitError(DeltaError):
    """HTTP 429, or the local budget/breaker refused the call. `retry_after` is in seconds."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class CircuitOpenError(RateLimitError):
    """Reads are paused locally after repeated 429s. No request was sent."""


class AuthBlockedError(AuthError):
    """These credentials were rejected recently; requests with them are suppressed locally for a cooldown."""


class InsufficientMarginError(DeltaError):
    """The order or position change needs more margin than is available."""


class OrderRejectedError(DeltaError):
    """Delta rejected an order for a reason other than margin (e.g. bad price, size or reduce-only conflict)."""


class OrdersNotEnabledError(DeltaError):
    """Order placement was attempted on a client not explicitly built for orders (safety gate)."""


_IP_RE = re.compile(r"(?<![\w.:])((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{2,39}:[0-9a-fA-F:]{1,39})(?![\w.:])")


def find_ip(payload: Any) -> str | None:
    """Find an IPv4/IPv6 address anywhere in an error payload (Delta includes the caller's IP, but its exact field
    is UNVERIFIED for REST)."""
    texts: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif o is not None:
            texts.append(str(o))

    walk(payload)
    for text in texts:
        for cand in _IP_RE.findall(text):
            try:
                return str(ipaddress.ip_address(cand))
            except ValueError:
                continue
    return None


def _error_code(payload: Any) -> tuple[str | None, dict]:
    if not isinstance(payload, dict):
        return None, {}
    err = payload.get("error")
    if isinstance(err, dict):
        ctx = err.get("context") if isinstance(err.get("context"), dict) else {}
        return (str(err["code"]) if err.get("code") is not None else None), ctx
    if isinstance(err, str):
        return err, {}
    return None, {}


def _norm(code: str | None) -> str:
    return re.sub(r"[^a-z]", "", (code or "").lower())


def classify_error(status: int, payload: Any, headers: dict[str, str] | None = None,
                   environment: str = "") -> DeltaError:
    """Map an HTTP error response to a typed exception with a clear, secret-free message."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    code, ctx = _error_code(payload)
    n = _norm(code)
    env = f" ({environment})" if environment else ""

    if status == 429:
        retry_after = None
        if headers.get("x-rate-limit-reset"):
            try:
                retry_after = float(headers["x-rate-limit-reset"]) / 1000.0
            except ValueError:
                pass
        if retry_after is None and headers.get("retry-after"):
            try:
                retry_after = float(headers["retry-after"])
            except ValueError:
                pass
        return RateLimitError(f"Delta rate limit hit (HTTP 429){env}; retry after "
                              f"{retry_after if retry_after is not None else '?'} s",
                              status=status, code=code, payload=payload, retry_after=retry_after)

    if n in ("ipnotwhitelistedforapikey", "ipnotwhitelisted"):
        ip = find_ip(payload)
        where = f"Your IP as seen by Delta: {ip}." if ip else "Delta did not report the IP; check your public IP (IPv4 and IPv6)."
        return IPNotWhitelistedError(
            f"IP address not whitelisted for this API key{env}. {where} Add it under Delta > API Management, "
            "or run from the whitelisted static-IP host.", status=status, code=code, payload=payload, ip=ip)
    if n in ("expiredsignature", "signatureexpired"):
        return SignatureExpiredError(
            f"Signature expired{env}: the request reached Delta more than 5 s after signing. Check the system clock.",
            status=status, code=code, payload=payload,
            server_time=ctx.get("server_time"), request_time=ctx.get("request_time"))
    if n == "invalidapikey":
        return AuthError(f"Invalid API key{env}: it does not exist, was regenerated, or belongs to the other "
                         "environment (testnet keys work only on testnet, production keys only on production).",
                         status=status, code=code, payload=payload)
    if n == "signaturemismatch":
        return AuthError(f"Signature mismatch{env}: wrong API secret, or the signed method/path/query/body differs "
                         "from the request.", status=status, code=code, payload=payload)
    if n == "unauthorizedapiaccess":
        return PermissionDeniedError(f"API key not authorised for this endpoint{env}: enable the needed permission "
                                     "(Read Data / Trading) on the key.", status=status, code=code, payload=payload)
    if n == "unauthorized" or status == 401:
        return AuthError(f"Unauthorized{env} (HTTP {status}, code {code!r}).", status=status, code=code, payload=payload)
    if n in ("insufficientmargin", "closepositioninsufficientmargin"):
        return InsufficientMarginError(f"Insufficient margin{env} ({code}).", status=status, code=code, payload=payload)
    if status == 403:
        return CDNForbiddenError(f"Request blocked by Delta's CDN{env} (HTTP 403). The User-Agent may be missing or "
                                 "the IP blocked.", status=status, code=code, payload=None)
    if status >= 500:
        return DeltaServerError(f"Delta server error{env} (HTTP {status}).", status=status, code=code, payload=payload)
    return DeltaError(f"Delta API error{env} (HTTP {status}, code {code!r}).", status=status, code=code, payload=payload)
