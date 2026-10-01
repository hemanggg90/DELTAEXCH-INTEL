"""DeltaClient against mocked HTTP: signing, retries, typed errors, order safety, caching."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

import pytest
import requests
import responses

from delta_intelligence.brokers.cache import TtlCache
from delta_intelligence.brokers.delta_api_client import USER_AGENT, DeltaClient, build_query, dumps_body, sign
from delta_intelligence.brokers.errors import (
    AuthBlockedError,
    AuthError,
    CDNForbiddenError,
    CircuitOpenError,
    DeltaConfigError,
    InsufficientMarginError,
    IPNotWhitelistedError,
    OrdersNotEnabledError,
    OrderStateUnknownError,
    PermissionDeniedError,
    RateLimitError,
)
from delta_intelligence.brokers.rate_limit import RateLimiter
from delta_intelligence.config.settings import DeltaLimits

BASE = "https://api.test"
KEY, SECRET = "test-key-abc123", "test-secret-xyz789"


def make_client(fake_clock, *, key: str = KEY, secret: str = SECRET, allow_orders: bool = False) -> DeltaClient:
    limiter = RateLimiter(DeltaLimits(), clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock)
    return DeltaClient(BASE, key, secret, environment="TESTNET", limiter=limiter, allow_orders=allow_orders,
                       clock=fake_clock, sleep=fake_clock.sleep, ticker_cache=TtlCache(fake_clock),
                       product_cache=TtlCache(fake_clock))


def expected_signature(request: requests.PreparedRequest) -> str:
    parts = urlsplit(request.url)
    query = f"?{parts.query}" if parts.query else ""
    body = request.body.decode() if isinstance(request.body, bytes) else (request.body or "")
    prehash = request.method + request.headers["timestamp"] + parts.path + query + body
    return hmac.new(SECRET.encode(), prehash.encode(), hashlib.sha256).hexdigest()


# ---- pure signing helpers ------------------------------------------------------------------------------------------
def test_sign_matches_documented_prehash_format() -> None:
    prehash = "GET1542110948/v2/orders?product_id=1&state=open"
    want = hmac.new(b"s3cret", prehash.encode(), hashlib.sha256).hexdigest()
    assert sign("s3cret", "get", "1542110948", "/v2/orders", "?product_id=1&state=open") == want


def test_build_query_and_body() -> None:
    assert build_query(None) == "" and build_query({"a": None}) == ""
    assert build_query({"b": 2, "a": True, "c": None, "ids": [1, 2]}) == "?b=2&a=true&ids=1%2C2"
    assert dumps_body({"a": 1, "b": "x"}) == '{"a":1,"b":"x"}' and dumps_body(None) == ""


# ---- requests ------------------------------------------------------------------------------------------------------
def test_public_request_has_user_agent_and_no_auth_headers(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/tickers/BTCUSD", json={"success": True, "result": {"symbol": "BTCUSD"}})
    make_client(fake_clock)._result("GET", "/v2/tickers/BTCUSD")
    req = mocked.calls[0].request
    assert req.headers["User-Agent"] == USER_AGENT
    assert "api-key" not in req.headers and "signature" not in req.headers


def test_authenticated_request_is_correctly_signed(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/orders", json={"success": True, "result": []})
    make_client(fake_clock).get_open_orders([27, 3136])
    req = mocked.calls[0].request
    assert req.headers["api-key"] == KEY
    assert req.headers["timestamp"] == str(int(fake_clock.t))
    assert req.headers["signature"] == expected_signature(req)


def test_post_body_is_signed_exactly_as_sent(mocked, fake_clock) -> None:
    mocked.post(f"{BASE}/v2/orders", json={"success": True, "result": {"id": 1}})
    make_client(fake_clock, allow_orders=True).place_order({"product_id": 27, "size": 1, "client_order_id": "c1"})
    req = mocked.calls[0].request
    assert req.body in (b'{"product_id":27,"size":1,"client_order_id":"c1"}',
                        '{"product_id":27,"size":1,"client_order_id":"c1"}')
    assert req.headers["signature"] == expected_signature(req)


def test_missing_credentials_raise_without_network(mocked, fake_clock) -> None:
    with pytest.raises(DeltaConfigError):
        make_client(fake_clock, key="", secret="").get_wallet_balances()
    assert len(mocked.calls) == 0


def test_retry_re_signs_with_fresh_timestamp(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/wallet/balances", status=502, json={"success": False})
    mocked.get(f"{BASE}/v2/wallet/balances", json={"success": True, "result": [{"asset_symbol": "USD"}]})
    assert make_client(fake_clock).get_wallet_balances() == [{"asset_symbol": "USD"}]
    first, second = (c.request for c in mocked.calls)
    assert first.headers["timestamp"] != second.headers["timestamp"]  # backoff advanced the clock
    assert second.headers["signature"] == expected_signature(second)


def test_429_honours_rate_limit_reset_header(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/tickers/BTCUSD", status=429, headers={"X-RATE-LIMIT-RESET": "4000"}, json={})
    mocked.get(f"{BASE}/v2/tickers/BTCUSD", json={"success": True, "result": {"symbol": "BTCUSD"}})
    client = make_client(fake_clock)
    assert client._result("GET", "/v2/tickers/BTCUSD") == {"symbol": "BTCUSD"}
    assert 4.0 in fake_clock.sleeps


def test_repeated_429_opens_breaker_then_fast_fails(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/tickers/BTCUSD", status=429, json={})
    client = make_client(fake_clock)
    with pytest.raises(RateLimitError):
        client._result("GET", "/v2/tickers/BTCUSD")
    calls = len(mocked.calls)
    assert calls == 3  # max_attempts
    with pytest.raises(CircuitOpenError):
        client._result("GET", "/v2/tickers/BTCUSD")
    assert len(mocked.calls) == calls  # no network while the breaker is open


def test_expired_signature_resyncs_clock_and_retries_once(mocked, fake_clock) -> None:
    server_time = int(fake_clock.t) + 60
    mocked.get(f"{BASE}/v2/wallet/balances", status=401, json={"success": False, "error": {
        "code": "expired_signature", "context": {"request_time": int(fake_clock.t), "server_time": server_time}}})
    mocked.get(f"{BASE}/v2/wallet/balances", json={"success": True, "result": []})
    client = make_client(fake_clock)
    assert client.get_wallet_balances() == []
    retry = mocked.calls[1].request
    assert abs(int(retry.headers["timestamp"]) - server_time) <= 1
    assert retry.headers["signature"] == expected_signature(retry)


def test_invalid_key_blocks_further_auth_requests(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/wallet/balances", status=401, json={"success": False, "error": {"code": "invalid_api_key"}})
    client = make_client(fake_clock)
    with pytest.raises(AuthError, match="other environment"):
        client.get_wallet_balances()
    with pytest.raises(AuthBlockedError):
        client.get_wallet_balances()
    assert len(mocked.calls) == 1


def test_ip_not_whitelisted_surfaces_the_ip(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/wallet/balances", status=401, json={
        "success": False, "error": {"code": "ip_not_whitelisted_for_api_key", "context": {"client_ip": "203.0.113.7"}}})
    with pytest.raises(IPNotWhitelistedError) as exc:
        make_client(fake_clock).get_wallet_balances()
    assert exc.value.ip == "203.0.113.7" and "203.0.113.7" in str(exc.value)


def test_permission_error_does_not_block_key(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/positions/margined", status=403,
               json={"error": "UnauthorizedApiAccess", "message": "Api Key not authorised"})
    client = make_client(fake_clock)
    with pytest.raises(PermissionDeniedError):
        client.get_positions()
    assert client.limiter.auth_block_remaining(KEY) == 0


def test_cdn_html_403(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/tickers/BTCUSD", status=403, body="<HTML>blocked</HTML>")
    with pytest.raises(CDNForbiddenError):
        make_client(fake_clock)._result("GET", "/v2/tickers/BTCUSD")


def test_offset_learned_from_request_in_time_header(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/rate_limits/quota", json={"current_quota": 9, "remaining_time_in_milliseconds": 1000},
               headers={"Request-In-Time": str(int((fake_clock.t + 3.0) * 1_000_000))})
    client = make_client(fake_clock)
    assert client.get_rate_limit_quota() == {"current_quota": 9, "remaining_time_in_milliseconds": 1000}
    assert client.clock_offset_sec == pytest.approx(3.0, abs=0.01)


# ---- order safety --------------------------------------------------------------------------------------------------
def test_orders_require_explicit_enablement(mocked, fake_clock) -> None:
    with pytest.raises(OrdersNotEnabledError):
        make_client(fake_clock).place_order({"product_id": 27, "size": 1, "client_order_id": "x"})
    assert len(mocked.calls) == 0


def test_order_needs_client_order_id(fake_clock) -> None:
    with pytest.raises(ValueError):
        make_client(fake_clock, allow_orders=True).place_order({"product_id": 27, "size": 1})


@pytest.mark.parametrize("kwargs,exc", [
    ({"status": 429, "json": {}}, RateLimitError),
    ({"status": 503, "json": {"success": False}}, OrderStateUnknownError),
    ({"body": requests.ConnectTimeout()}, OrderStateUnknownError),
    ({"status": 400, "json": {"success": False, "error": {"code": "insufficient_margin"}}}, InsufficientMarginError),
])
def test_orders_are_never_retried(mocked, fake_clock, kwargs, exc) -> None:
    mocked.post(f"{BASE}/v2/orders", **kwargs)
    mocked.post(f"{BASE}/v2/orders", json={"success": True, "result": {"id": 1}})
    with pytest.raises(exc):
        make_client(fake_clock, allow_orders=True).place_order({"product_id": 27, "size": 1, "client_order_id": "o1"})
    assert len(mocked.calls) == 1


def test_order_not_blocked_by_open_read_breaker(mocked, fake_clock) -> None:
    client = make_client(fake_clock, allow_orders=True)
    for _ in range(3):
        client.limiter.record_429("read")
    assert client.limiter.cooldown_remaining("read") > 0
    mocked.post(f"{BASE}/v2/orders", json={"success": True, "result": {"id": 5}})
    assert client.place_order({"product_id": 27, "size": 1, "client_order_id": "exit1"}) == {"id": 5}


# ---- caching / pagination ------------------------------------------------------------------------------------------
def test_products_paginate_and_cache(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/products", json={"success": True, "result": [{"symbol": "BTCUSD", "id": 27}],
                                            "meta": {"after": "CUR1"}},
               match=[responses.matchers.query_param_matcher(
                   {"contract_types": "perpetual_futures", "states": "live", "page_size": "500"})])
    mocked.get(f"{BASE}/v2/products", json={"success": True, "result": [{"symbol": "ETHUSD", "id": 3136}],
                                            "meta": {"after": None}},
               match=[responses.matchers.query_param_matcher(
                   {"contract_types": "perpetual_futures", "states": "live", "page_size": "500", "after": "CUR1"})])
    client = make_client(fake_clock)
    assert [p["symbol"] for p in client.get_products()] == ["BTCUSD", "ETHUSD"]
    assert client.get_product("ETHUSD")["id"] == 3136
    assert len(mocked.calls) == 2  # second lookup served from cache


def test_tickers_one_request_serves_all_symbols(mocked, fake_clock) -> None:
    mocked.get(f"{BASE}/v2/tickers", json={"success": True, "result": [
        {"symbol": "BTCUSD", "mark_price": "1"}, {"symbol": "ETHUSD", "mark_price": "2"}]})
    client = make_client(fake_clock)
    assert client.get_ticker("BTCUSD")["mark_price"] == "1"
    assert client.get_ticker("ETHUSD")["mark_price"] == "2"
    assert len(mocked.calls) == 1


def test_client_repr_and_logs_hide_secrets(fake_clock) -> None:
    text = repr(make_client(fake_clock))
    assert KEY not in text and SECRET not in text
