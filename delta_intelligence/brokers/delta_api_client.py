"""
DeltaClient: the single gateway to Delta Exchange India's REST API.

Every behaviour below was verified on 2026-10-01; see docs/DELTA_API_NOTES.md.

- **Signing** (docs section 2): `hex(HMAC_SHA256(secret, METHOD + timestamp + path + query_string + body))`, where
  the timestamp is in Unix seconds. A signature is valid for **5 s**, so every attempt is re-signed with a fresh
  timestamp.
- **Clock sync.** The local-to-server offset is learned from `expired_signature` errors (which carry
  `context.server_time`) and from the HTTP `Date` header. Signing uses the corrected time.
- **Budget** (docs section 3). Each call reserves its endpoint weight from the shared `LIMITER` before it is sent.
  - Reads retry 429s (honouring `X-RATE-LIMIT-RESET`), 5xx and network errors with backoff.
  - **Orders are throttled but never retried**, and a failure in transit raises `OrderStateUnknownError`: the caller
    must reconcile through the `client_order_id`.
  - Order placement additionally requires `allow_orders=True` at construction. Only `DeltaBroker` (P7) sets it,
    after its own LIVE checks.
- **Caches.** Products (1 h) and tickers (2 s) are shared process-wide through `brokers/cache.py`.
- **Secrets.** The key and secret are registered for log redaction and kept out of `repr()`. Headers are never
  logged.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import hmac
import json
import platform
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

import requests

from delta_intelligence import __version__
from delta_intelligence.brokers.cache import PRODUCT_CACHE, TICKER_CACHE, TtlCache
from delta_intelligence.brokers.errors import (
    AuthBlockedError,
    AuthError,
    CircuitOpenError,
    DeltaConfigError,
    DeltaError,
    DeltaNetworkError,
    DeltaServerError,
    InsufficientMarginError,
    OrderRejectedError,
    OrdersNotEnabledError,
    OrderStateUnknownError,
    PermissionDeniedError,
    RateLimitError,
    SignatureExpiredError,
    classify_error,
)
from delta_intelligence.brokers.rate_limit import LIMITER, RateLimiter
from delta_intelligence.config.settings import Settings, get_settings
from delta_intelligence.utils.logging_utils import log_event, register_secret

USER_AGENT = f"delta-intelligence/{__version__} python-{platform.python_version()}"

# Endpoint weights (docs section 3). Unlisted endpoints cost 1.
W_LIGHT = 1
W_READ = 3  # products, orderbook, tickers, open orders, positions, balances, OHLC
W_ORDER = 5  # place/edit/delete order, add position margin
W_HISTORY = 10  # order history, fills, transaction logs
W_BATCH = 25

# Clock offsets smaller than this are ignored when learned from the 1 s-resolution Date header.
_DATE_HEADER_DEADBAND_SEC = 1.5
_PRECISE_DEADBAND_SEC = 0.25


def sign(secret: str, method: str, timestamp: str, path: str, query_string: str = "", body: str = "") -> str:
    """Delta's request signature. `query_string` includes the leading '?' when non-empty."""
    prehash = method.upper() + timestamp + path + query_string + body
    return hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()


def build_query(params: Mapping[str, Any] | None) -> str:
    """'?a=1&b=x' with None values dropped and booleans lower-cased; '' when empty. The exact same string is both
    signed and sent, so the order is preserved."""
    if not params:
        return ""
    clean: list[tuple[str, str]] = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        clean.append((k, str(v)))
    return ("?" + urlencode(clean)) if clean else ""


def dumps_body(body: Any) -> str:
    return "" if body is None else json.dumps(body, separators=(",", ":"))


class DeltaClient:
    """Thread-safe REST client. Build one per (environment, credentials) pair; caches and the limiter are shared."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        api_secret: str = "",
        *,
        environment: str = "",
        limits_settings: Settings | None = None,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_orders: bool = False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        ticker_cache: TtlCache | None = None,
        product_cache: TtlCache | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.environment = environment
        self._api_key = api_key
        self._api_secret = api_secret
        register_secret(api_key)
        register_secret(api_secret)
        self._settings = limits_settings
        self.session = session or requests.Session()
        self.limiter = limiter or LIMITER
        self.allow_orders = allow_orders
        self.clock = clock
        self.sleep = sleep
        self.ticker_cache = ticker_cache or TICKER_CACHE
        self.product_cache = product_cache or PRODUCT_CACHE
        self.clock_offset_sec = 0.0  # server time - local time
        self.clock_offset_measured = False
        self.last_measured_offset: float | None = None  # raw latest estimate (for display)
        self.last_request_at: float | None = None

    def __repr__(self) -> str:  # never show credentials
        return f"DeltaClient(base_url={self.base_url!r}, environment={self.environment!r}, " \
               f"has_credentials={self.has_credentials}, allow_orders={self.allow_orders})"

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key and self._api_secret)

    # ---- clock ---------------------------------------------------------------------------------------------------
    def server_now(self) -> float:
        return self.clock() + self.clock_offset_sec

    def _timestamp(self) -> str:
        return str(int(self.server_now()))

    def _learn_offset(self, headers: Mapping[str, str], sent_at: float, received_at: float) -> None:
        """Estimate the server-local offset. Two sources:
        - `Request-In-Time`: server receive time in µs, observed on every response on 2026-10-01; precise.
        - `Date`: 1 s resolution; used only when the first is absent."""
        midpoint = (sent_at + received_at) / 2
        req_in = headers.get("Request-In-Time")
        if req_in:
            try:
                estimate = int(req_in) / 1_000_000 - midpoint
            except ValueError:
                estimate = None
            if estimate is not None:
                self.last_measured_offset = estimate
                if abs(estimate - self.clock_offset_sec) > _PRECISE_DEADBAND_SEC:
                    self.clock_offset_sec = estimate
                self.clock_offset_measured = True
                return
        date_header = headers.get("Date")
        if not date_header:
            return
        try:
            server = email.utils.parsedate_to_datetime(date_header).timestamp()
        except (TypeError, ValueError):
            return
        estimate = server + 0.5 - midpoint  # Date truncates to the second: +0.5 centres it
        self.last_measured_offset = estimate
        if abs(estimate - self.clock_offset_sec) > _DATE_HEADER_DEADBAND_SEC:
            self.clock_offset_sec = estimate
        self.clock_offset_measured = True

    def _learn_offset_from_server_time(self, server_time: Any) -> bool:
        try:
            st = float(server_time)
        except (TypeError, ValueError):
            return False
        self.clock_offset_sec = st - self.clock()
        log_event("delta_client", f"Clock re-synced from Delta server time: offset {self.clock_offset_sec:+.1f}s",
                  level="WARNING")
        return True

    # ---- core request --------------------------------------------------------------------------------------------
    def _headers(self, method: str, path: str, query: str, body: str, auth: bool) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            ts = self._timestamp()
            headers["api-key"] = self._api_key
            headers["timestamp"] = ts
            headers["signature"] = sign(self._api_secret, method, ts, path, query, body)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        auth: bool = False,
        weight: int = W_LIGHT,
        kind: str = "read",
    ) -> dict:
        """Send one logical request and return the decoded JSON payload (`{"success": true, "result": ...}`)."""
        method = method.upper()
        if not path.startswith("/v2/"):
            raise ValueError("path must start with /v2/")
        if kind == "order" and not self.allow_orders:
            raise OrdersNotEnabledError("this DeltaClient was not built for order placement")
        if auth:
            if not self.has_credentials:
                raise DeltaConfigError(f"DELTA_API_KEY / DELTA_API_SECRET are not set for {self.environment or 'this'} "
                                       "environment")
            blocked = self.limiter.auth_block_remaining(self._api_key)
            if blocked > 0:
                raise AuthBlockedError(f"Delta rejected these credentials recently; authenticated requests paused for "
                                       f"{blocked:.0f}s (or until the key changes)")
        if kind == "read":
            cooldown = self.limiter.cooldown_remaining("read")
            if cooldown > 0:
                raise CircuitOpenError(f"Delta reads paused for {cooldown:.0f}s after repeated rate-limit responses",
                                       retry_after=cooldown)

        query = build_query(params)
        body_str = dumps_body(body)
        url = self.base_url + path + query
        limits = self.settings.delta
        max_attempts = 1 if kind == "order" else max(1, limits.max_attempts)
        resynced = False
        attempt = 0

        while True:
            attempt += 1
            self.limiter.acquire(weight, kind)
            headers = self._headers(method, path, query, body_str, auth)  # fresh timestamp + signature every attempt
            sent_at = self.clock()
            try:
                resp = self.session.request(method, url, data=body_str or None, headers=headers,
                                            timeout=(limits.connect_timeout_sec, limits.read_timeout_sec))
            except (requests.Timeout, requests.ConnectionError) as exc:
                if kind == "order":
                    raise OrderStateUnknownError(
                        f"order request to {path} failed in transit ({type(exc).__name__}); the order MAY have been "
                        "accepted - reconcile by client_order_id before doing anything else") from None
                if attempt < max_attempts:
                    self.sleep(limits.backoff_sec * 2 ** (attempt - 1))
                    continue
                raise DeltaNetworkError(f"network error calling Delta {path}: {type(exc).__name__}") from None
            received_at = self.clock()
            self.last_request_at = received_at
            self._learn_offset(resp.headers, sent_at, received_at)

            try:
                payload: Any = resp.json()
            except ValueError:
                payload = None

            ok = 200 <= resp.status_code < 300 and not (isinstance(payload, dict) and payload.get("success") is False)
            if ok:
                self.limiter.record_success(kind)
                return payload if isinstance(payload, dict) else {"success": True, "result": payload}

            err = classify_error(resp.status_code, payload, dict(resp.headers), self.environment)

            if isinstance(err, RateLimitError):
                opened = self.limiter.record_429(kind, err.retry_after)
                if kind == "order" or opened or attempt >= max_attempts:
                    raise err
                wait = err.retry_after if err.retry_after is not None else limits.backoff_sec * 2 ** (attempt - 1)
                if wait > limits.max_read_wait_sec:
                    raise err
                self.sleep(wait)
                continue

            if isinstance(err, SignatureExpiredError):
                learned = self._learn_offset_from_server_time(err.server_time)
                # Re-signing with the corrected clock is safe for reads. An expired order was rejected outright
                # (never accepted), but per the no-retry rule the caller decides whether to resend it.
                if kind == "read" and learned and not resynced:
                    resynced = True
                    attempt -= 1  # a clock fix doesn't consume a retry
                    continue
                raise err

            if isinstance(err, AuthError) and not isinstance(err, PermissionDeniedError):
                self.limiter.record_auth_failure(self._api_key)
                log_event("delta_client", str(err), level="ERROR", path=path)
                raise err

            if kind == "order":
                if resp.status_code >= 500:
                    raise OrderStateUnknownError(
                        f"Delta returned HTTP {resp.status_code} for an order; the order MAY have been accepted - "
                        "reconcile by client_order_id", status=resp.status_code, code=err.code, payload=payload)
                if isinstance(err, InsufficientMarginError):
                    raise err
                raise OrderRejectedError(f"Delta rejected the order: {err.code or resp.status_code}",
                                         status=resp.status_code, code=err.code, payload=payload)

            if isinstance(err, DeltaServerError) and attempt < max_attempts:
                self.sleep(limits.backoff_sec * 2 ** (attempt - 1))
                continue
            raise err

    def _result(self, *args: Any, **kwargs: Any) -> Any:
        return self.request(*args, **kwargs).get("result")

    # ---- public market data --------------------------------------------------------------------------------------
    def get_products(self, contract_types: str = "perpetual_futures", states: str = "live",
                     max_age: float | None = None) -> list[dict]:
        """All products of the given type, following cursor pagination. Cached (1 h by default)."""
        ttl = self.settings.delta.products_cache_ttl_sec if max_age is None else max_age

        def load() -> list[dict]:
            out: list[dict] = []
            after: str | None = None
            for _ in range(50):  # hard stop against a pagination loop
                payload = self.request("GET", "/v2/products", params={
                    "contract_types": contract_types, "states": states, "page_size": 500, "after": after},
                    weight=W_READ)
                out.extend(payload.get("result") or [])
                after = (payload.get("meta") or {}).get("after")
                if not after:
                    break
            return out

        return self.product_cache.get(("products", self.base_url, contract_types, states), load, ttl)

    def get_product(self, symbol: str) -> dict:
        for p in self.get_products():
            if p.get("symbol") == symbol:
                return p
        raise DeltaError(f"product {symbol!r} not found among live perpetuals on {self.environment or self.base_url}")

    def get_tickers(self, contract_types: str = "perpetual_futures", max_age: float | None = None,
                    stale_ok_for: float = 0.0) -> dict[str, dict]:
        """{symbol: ticker} for every product of the type, from ONE request shared by all callers. Use the
        `timestamp` field (µs); `time` was observed 24 h stale (docs section 5)."""
        ttl = self.settings.delta.ticker_cache_ttl_sec if max_age is None else max_age

        def load() -> dict[str, dict]:
            rows = self._result("GET", "/v2/tickers", params={"contract_types": contract_types}, weight=W_READ) or []
            return {r["symbol"]: r for r in rows if r.get("symbol")}

        return self.ticker_cache.get(("tickers", self.base_url, contract_types), load, ttl, stale_ok_for=stale_ok_for)

    def get_ticker(self, symbol: str, max_age: float | None = None) -> dict:
        tickers = self.get_tickers(max_age=max_age)
        if symbol in tickers:
            return tickers[symbol]
        return self._result("GET", f"/v2/tickers/{symbol}", weight=W_READ)

    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> list[dict]:
        """Raw candles (newest first, may include the forming bar). `start`/`end` in Unix seconds. Public."""
        return self._result("GET", "/v2/history/candles", params={
            "resolution": resolution, "symbol": symbol, "start": int(start), "end": int(end)}, weight=W_READ) or []

    def get_orderbook(self, symbol: str, depth: int = 20) -> dict:
        return self._result("GET", f"/v2/l2orderbook/{symbol}", params={"depth": depth}, weight=W_READ) or {}

    def get_rate_limit_quota(self) -> dict:
        """Observed shape (2026-10-01): top-level {"current_quota", "remaining_time_in_milliseconds"}, no "result".
        Whether current_quota is used or remaining weight is UNVERIFIED."""
        payload = self.request("GET", "/v2/rate_limits/quota", weight=W_LIGHT)
        quota = payload.get("result") if isinstance(payload.get("result"), dict) else             {k: v for k, v in payload.items() if k != "success"}
        self.limiter.record_server_quota(quota)
        return quota

    # ---- private (read) ------------------------------------------------------------------------------------------
    def get_wallet_balances(self) -> list[dict]:
        return self._result("GET", "/v2/wallet/balances", auth=True, weight=W_READ) or []

    def get_positions(self) -> list[dict]:
        return self._result("GET", "/v2/positions/margined", auth=True, weight=W_READ) or []

    def get_open_orders(self, product_ids: list[int] | None = None) -> list[dict]:
        params = {"states": "open,pending"}
        if product_ids:
            params["product_ids"] = ",".join(str(i) for i in product_ids)
        return self._result("GET", "/v2/orders", params=params, auth=True, weight=W_READ) or []

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        try:
            return self._result("GET", f"/v2/orders/client_order_id/{client_order_id}", auth=True, weight=W_LIGHT)
        except DeltaError as exc:
            if exc.status == 404:
                return None
            raise

    # ---- orders (only with allow_orders=True; used by DeltaBroker in P7) -----------------------------------------
    def place_order(self, order: dict) -> dict:
        """POST /v2/orders exactly once. Never retried; see OrderStateUnknownError."""
        if not order.get("client_order_id"):
            raise ValueError("every order needs a client_order_id (for reconciliation)")
        return self._result("POST", "/v2/orders", body=order, auth=True, weight=W_ORDER, kind="order")


# ---- factories ---------------------------------------------------------------------------------------------------
_public_clients: dict[str, DeltaClient] = {}


def public_client(settings: Settings | None = None) -> DeltaClient:
    """Unauthenticated client for the MARKET-DATA environment (production by default in PAPER mode)."""
    s = settings or get_settings()
    base = s.data_rest_base_url
    if base not in _public_clients:
        _public_clients[base] = DeltaClient(base, environment=s.data_env, limits_settings=settings)
    return _public_clients[base]


def account_client(settings: Settings | None = None) -> DeltaClient:
    """Authenticated, READ-ONLY client for the trading environment (balances, positions, orders lookup)."""
    s = settings or get_settings()
    return DeltaClient(s.rest_base_url, s.delta_api_key, s.delta_api_secret, environment=s.delta_env,
                       limits_settings=settings)


def utc_from_server(client: DeltaClient) -> dt.datetime:
    return dt.datetime.fromtimestamp(client.server_now(), dt.timezone.utc)
