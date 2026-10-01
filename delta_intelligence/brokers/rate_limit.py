"""
Process-wide request budget for Delta's REST API.

Delta charges each endpoint a *weight* against a quota of 20,000 per fixed 5-minute window, per user (authenticated)
or per IP (public). See docs/DELTA_API_NOTES.md section 3. Every request the app makes goes through `LIMITER`, so
the scanner, the WebSocket bootstrap and every browser tab share one budget.

- `acquire(weight, kind)` blocks until the weight fits in a sliding 300 s window. A sliding window is at least as
  strict as Delta's fixed one.
  - Reads may use `read_budget_fraction` of the quota; orders may use `order_budget_fraction`, so exits keep headroom.
  - Orders are also spaced by `min_order_interval_sec`.
  - Reads that would wait longer than `max_read_wait_sec` raise `RateLimitError` instead of blocking.
- `record_429` / `record_success` drive a circuit breaker. After several consecutive 429s, reads fast-fail for a
  cooldown (doubling to a maximum, and never shorter than Delta's `X-RATE-LIMIT-RESET`).
  **Orders are never blocked by the breaker**, so an exit can always be attempted.
- `record_auth_failure(key)` suppresses requests with credentials Delta just rejected. Only a SHA-256 fingerprint of
  the key is kept, never the key itself.
- `snapshot()` feeds the health page.

It cannot see requests from other processes using the same account or IP. Run one instance.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from collections.abc import Callable

from delta_intelligence.brokers.errors import RateLimitError
from delta_intelligence.config.settings import DeltaLimits, get_settings
from delta_intelligence.utils.logging_utils import log_event

KINDS = ("read", "order")


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else ""


class RateLimiter:
    def __init__(self, limits: DeltaLimits | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, wall: Callable[[], float] = time.time):
        self._limits = limits
        self.clock = clock
        self.sleep = sleep
        self.wall = wall
        self._state = threading.Lock()
        self.reset()

    @property
    def limits(self) -> DeltaLimits:
        return self._limits or get_settings().delta

    def reset(self) -> None:
        with self._state:
            self._used: deque[tuple[float, int, str]] = deque()  # (t, weight, kind) in the last window
            self._calls: deque[tuple[float, str]] = deque(maxlen=20_000)
            self._next_order_at = 0.0
            self._total_429 = {k: 0 for k in KINDS}
            self._consecutive_429 = 0
            self._breaker_opens = 0
            self._cooldown_until = 0.0
            self._auth_failed_fp = ""
            self._auth_failed_until = 0.0
            self._last_ok_wall: float | None = None
            self._last_server_quota: dict | None = None
            self._network_errors = 0

    # ---- budget ------------------------------------------------------------------------------------------------
    def _cap(self, kind: str) -> float:
        frac = self.limits.order_budget_fraction if kind == "order" else self.limits.read_budget_fraction
        return self.limits.quota_per_window * frac

    def _prune(self, now: float) -> None:
        window = self.limits.window_sec
        while self._used and now - self._used[0][0] >= window:
            self._used.popleft()

    def used_weight(self, window: float | None = None) -> int:
        now = self.clock()
        with self._state:
            self._prune(now)
            w = self.limits.window_sec if window is None else window
            return sum(wt for t, wt, _ in self._used if now - t < w)

    def _wait_for_budget(self, weight: int, kind: str, now: float) -> float:
        """Seconds until `weight` fits under the cap for `kind`, assuming no new usage arrives."""
        self._prune(now)
        cap = self._cap(kind)
        total = sum(wt for _, wt, _ in self._used)
        if total + weight <= cap:
            return 0.0
        excess = total + weight - cap
        freed = 0
        for t, wt, _ in self._used:
            freed += wt
            if freed >= excess:
                return max(0.0, t + self.limits.window_sec - now)
        return self.limits.window_sec

    def acquire(self, weight: int, kind: str = "read") -> None:
        """Reserve `weight` units, blocking while the budget (or order spacing) requires it."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        # Check-and-reserve is atomic; sleeping happens OUTSIDE the lock, so a read waiting for budget never
        # holds up an order (an exit must not queue behind reads).
        while True:
            with self._state:
                now = self.clock()
                wait = self._wait_for_budget(weight, kind, now)
                if kind == "order":
                    wait = max(wait, self._next_order_at - now)
                if wait <= 0:
                    self._used.append((now, weight, kind))
                    self._calls.append((now, kind))
                    if kind == "order":
                        self._next_order_at = now + self.limits.min_order_interval_sec
                    return
            if kind == "read" and wait > self.limits.max_read_wait_sec:
                raise RateLimitError(
                    f"local request budget exhausted ({self.used_weight()} of "
                    f"{self.limits.quota_per_window} weight used in the last 5 min); read refused",
                    retry_after=wait)
            self.sleep(wait)

    # ---- circuit breaker ---------------------------------------------------------------------------------------
    def cooldown_remaining(self, kind: str = "read") -> float:
        if kind == "order":
            return 0.0
        return max(0.0, self._cooldown_until - self.clock())

    def record_429(self, kind: str, retry_after: float | None = None) -> bool:
        """Note a 429. Returns True if this opened (or re-opened) the read breaker."""
        opened = False
        with self._state:
            self._total_429[kind] += 1
            if kind == "read":
                self._consecutive_429 += 1
                if self._consecutive_429 >= self.limits.breaker_threshold:
                    self._breaker_opens += 1
                    cooldown = min(self.limits.breaker_cooldown_sec * 2 ** (self._breaker_opens - 1),
                                   self.limits.breaker_max_cooldown_sec)
                    self._cooldown_until = self.clock() + cooldown
                    self._consecutive_429 = self.limits.breaker_threshold - 1  # half-open: one more 429 re-opens
                    opened = True
            if retry_after and kind == "read":
                # Delta told us exactly when the window resets: never resume reads before then.
                self._cooldown_until = max(self._cooldown_until, self.clock() + retry_after)
        log_event("rate_limit", f"Delta 429 on {kind} call"
                  + (f"; reads paused for {self.cooldown_remaining():.0f}s" if opened else ""), level="WARNING")
        return opened

    def record_success(self, kind: str) -> None:
        with self._state:
            self._last_ok_wall = self.wall()
            if kind == "read":
                self._consecutive_429 = 0
                if self.clock() >= self._cooldown_until:
                    self._breaker_opens = 0

    def record_network_error(self) -> None:
        """Timeouts / dropped connections (counted for the health page; they never open the breaker)."""
        with self._state:
            self._network_errors += 1

    def record_server_quota(self, quota: dict) -> None:
        with self._state:
            self._last_server_quota = dict(quota)

    # ---- rejected credentials ----------------------------------------------------------------------------------
    def record_auth_failure(self, api_key: str) -> None:
        fp = key_fingerprint(api_key)
        with self._state:
            first = fp != self._auth_failed_fp or self.clock() >= self._auth_failed_until
            self._auth_failed_fp = fp
            self._auth_failed_until = self.clock() + self.limits.auth_failure_cooldown_sec
        if first:
            log_event("rate_limit", "Delta rejected the API credentials; authenticated requests with this key are "
                      f"paused for {self.limits.auth_failure_cooldown_sec:.0f}s or until the key changes",
                      level="ERROR")

    def auth_block_remaining(self, api_key: str) -> float:
        fp = key_fingerprint(api_key)
        if not fp or fp != self._auth_failed_fp:
            return 0.0
        return max(0.0, self._auth_failed_until - self.clock())

    def clear_auth_block(self) -> None:
        with self._state:
            self._auth_failed_fp, self._auth_failed_until = "", 0.0

    # ---- observability -----------------------------------------------------------------------------------------
    def snapshot(self, window: float = 60.0) -> dict:
        now = self.clock()
        with self._state:
            self._prune(now)
            return {
                "weight_last_window": sum(wt for t, wt, _ in self._used if now - t < window),
                "weight_last_5min": sum(wt for _, wt, _ in self._used),
                "quota_per_5min": self.limits.quota_per_window,
                "read_cap": self._cap("read"),
                "order_cap": self._cap("order"),
                "calls_last_window": {k: sum(1 for t, kk in self._calls if kk == k and now - t < window)
                                      for k in KINDS},
                "total_429": dict(self._total_429),
                "cooldown_remaining": max(0.0, self._cooldown_until - now),
                "auth_block_remaining": max(0.0, self._auth_failed_until - now),
                "last_ok_age_sec": None if self._last_ok_wall is None else self.wall() - self._last_ok_wall,
                "server_quota": self._last_server_quota,
                "network_errors": self._network_errors,
                "window_sec": window,
            }


LIMITER = RateLimiter()
