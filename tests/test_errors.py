from __future__ import annotations

import pytest

from delta_intelligence.brokers.errors import (
    AuthError,
    DeltaServerError,
    IPNotWhitelistedError,
    InsufficientMarginError,
    PermissionDeniedError,
    RateLimitError,
    SignatureExpiredError,
    classify_error,
    find_ip,
)


def test_string_error_unauthorized() -> None:
    err = classify_error(401, {"error": "unauthorized", "success": False})
    assert type(err) is AuthError


def test_expired_signature_carries_server_time() -> None:
    err = classify_error(401, {"error": {"code": "expired_signature",
                                         "context": {"request_time": 1, "server_time": 61}}})
    assert isinstance(err, SignatureExpiredError) and err.server_time == 61


def test_docs_style_signature_expired_name_also_mapped() -> None:
    assert isinstance(classify_error(401, {"error": "SignatureExpired"}), SignatureExpiredError)


@pytest.mark.parametrize("payload,ip", [
    ({"error": {"code": "ip_not_whitelisted_for_api_key", "context": {"client_ip": "198.51.100.4"}}}, "198.51.100.4"),
    ({"status": "ip_not_whitelisted", "message": "IP address not whitelisted. Your IP: 2001:db8::1"}, "2001:db8::1"),
    ({"error": {"code": "ip_not_whitelisted_for_api_key"}}, None),
])
def test_ip_not_whitelisted(payload, ip) -> None:
    payload = dict(payload)
    payload.setdefault("error", {"code": "ip_not_whitelisted"})
    err = classify_error(401, payload)
    assert isinstance(err, IPNotWhitelistedError) and err.ip == ip
    assert (ip or "did not report") in str(err)


def test_find_ip_ignores_times_and_numbers() -> None:
    assert find_ip({"message": "at 18:34:18 code 1001"}) is None


def test_429_retry_after_from_ms_header() -> None:
    err = classify_error(429, {}, {"X-RATE-LIMIT-RESET": "2500"})
    assert isinstance(err, RateLimitError) and err.retry_after == 2.5


def test_other_mappings() -> None:
    assert isinstance(classify_error(400, {"error": {"code": "insufficient_margin"}}), InsufficientMarginError)
    assert isinstance(classify_error(403, {"error": "UnauthorizedApiAccess"}), PermissionDeniedError)
    assert isinstance(classify_error(502, None), DeltaServerError)
    assert "testnet keys" in str(classify_error(401, {"error": {"code": "invalid_api_key"}}))
