from __future__ import annotations

import threading
import time

import pytest

from delta_intelligence.brokers.cache import TtlCache
from delta_intelligence.brokers.errors import RateLimitError
from delta_intelligence.brokers.rate_limit import RateLimiter, key_fingerprint
from delta_intelligence.config.settings import DeltaLimits


def limiter(clock, **kw) -> RateLimiter:
    return RateLimiter(DeltaLimits(**kw), clock=clock, sleep=clock.sleep, wall=clock)


def test_under_budget_does_not_wait(fake_clock) -> None:
    lim = limiter(fake_clock)
    for _ in range(100):
        lim.acquire(3)
    assert fake_clock.sleeps == [] and lim.used_weight() == 300


def test_read_waits_for_window_to_free_budget(fake_clock) -> None:
    lim = limiter(fake_clock, quota_per_window=100, read_budget_fraction=0.5, max_read_wait_sec=1000)
    for _ in range(10):
        lim.acquire(5)  # 50 = read cap
    fake_clock.advance(100)
    lim.acquire(5)
    assert fake_clock.sleeps == [pytest.approx(200)]  # waits until the first reservation leaves the 300 s window


def test_read_refused_when_wait_too_long(fake_clock) -> None:
    lim = limiter(fake_clock, quota_per_window=100, read_budget_fraction=0.5, max_read_wait_sec=30)
    for _ in range(10):
        lim.acquire(5)
    with pytest.raises(RateLimitError):
        lim.acquire(5)


def test_orders_keep_headroom_when_reads_exhausted(fake_clock) -> None:
    lim = limiter(fake_clock, quota_per_window=100, read_budget_fraction=0.5, order_budget_fraction=0.95)
    for _ in range(10):
        lim.acquire(5)
    lim.acquire(5, "order")
    assert fake_clock.sleeps == []


def test_orders_are_spaced(fake_clock) -> None:
    lim = limiter(fake_clock, min_order_interval_sec=0.2)
    lim.acquire(5, "order")
    lim.acquire(5, "order")
    assert fake_clock.sleeps == [pytest.approx(0.2)]


def test_breaker_opens_doubles_and_spares_orders(fake_clock) -> None:
    lim = limiter(fake_clock, breaker_threshold=3, breaker_cooldown_sec=30, breaker_max_cooldown_sec=120)
    assert not lim.record_429("read") and not lim.record_429("read")
    assert lim.record_429("read")
    assert lim.cooldown_remaining("read") == pytest.approx(30)
    assert lim.cooldown_remaining("order") == 0
    fake_clock.advance(31)
    assert lim.record_429("read")  # half-open: one more 429 re-opens, doubled
    assert lim.cooldown_remaining("read") == pytest.approx(60)


def test_retry_after_extends_cooldown(fake_clock) -> None:
    lim = limiter(fake_clock)
    lim.record_429("read", retry_after=90)
    assert lim.cooldown_remaining("read") == pytest.approx(90)


def test_success_resets_breaker(fake_clock) -> None:
    lim = limiter(fake_clock, breaker_threshold=2)
    lim.record_429("read")
    lim.record_success("read")
    assert not lim.record_429("read")


def test_auth_block_keyed_by_fingerprint(fake_clock) -> None:
    lim = limiter(fake_clock, auth_failure_cooldown_sec=300)
    lim.record_auth_failure("old-key-123")
    assert lim.auth_block_remaining("old-key-123") == pytest.approx(300)
    assert lim.auth_block_remaining("new-key-456") == 0
    snap = str(lim.snapshot())
    assert "old-key-123" not in snap
    assert key_fingerprint("old-key-123") != "old-key-123"


def test_snapshot_counts(fake_clock) -> None:
    lim = limiter(fake_clock)
    lim.acquire(3)
    lim.acquire(5, "order")
    lim.record_429("read")
    snap = lim.snapshot()
    assert snap["weight_last_window"] == 8
    assert snap["calls_last_window"] == {"read": 1, "order": 1}
    assert snap["total_429"]["read"] == 1


# ---- TtlCache ------------------------------------------------------------------------------------------------------
def test_cache_single_flight_under_concurrency() -> None:
    cache = TtlCache()
    calls = []

    def loader():
        calls.append(1)
        time.sleep(0.05)
        return "v"

    threads = [threading.Thread(target=lambda: cache.get("k", loader, 10)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def test_cache_ttl_and_stale_fallback(fake_clock) -> None:
    cache = TtlCache(clock=fake_clock)
    assert cache.get("k", lambda: 1, max_age=5) == 1
    fake_clock.advance(6)

    def boom():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        cache.get("k", boom, max_age=5)
    assert cache.get("k", boom, max_age=5, stale_ok_for=60) == 1
    assert cache.get("k", lambda: 2, max_age=5) == 2  # exceptions were not cached
