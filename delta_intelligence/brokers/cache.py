"""
Shared, single-flight caches for Delta reads.

One network call serves every caller inside the TTL. Concurrent callers asking for the same key wait for the single
in-flight request instead of firing their own, so ten open tabs cost the same as one. This is lifted from the
reference project's `dhan_cache.TtlCache`.

`/v2/tickers?contract_types=perpetual_futures` returns every perpetual in ONE call (weight 3), so ticker data needs
no per-symbol batching: one cached response serves all symbols.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any


class TtlCache:
    """Keyed value cache with a per-key lock, so concurrent misses trigger a single `loader()` call."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._values: dict[Hashable, tuple[float, Any]] = {}
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, key: Hashable, loader: Callable[[], Any], max_age: float, stale_ok_for: float = 0.0) -> Any:
        """Return the cached value if younger than `max_age`, else call `loader()` once (single flight).

        If the loader raises and `stale_ok_for` > 0, a cached value up to that age is returned instead. That is fine
        for display, but never for exit decisions. Exceptions are never cached.
        """
        with self._guard:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            entry = self._values.get(key)
            if entry is not None and self.clock() - entry[0] < max_age:  # strict: max_age=0 never hits
                return entry[1]
            try:
                value = loader()
            except Exception:
                if stale_ok_for > 0 and entry is not None and self.clock() - entry[0] < stale_ok_for:
                    return entry[1]
                raise
            self._values[key] = (self.clock(), value)
            return value

    def peek(self, key: Hashable, max_age: float) -> Any:
        entry = self._values.get(key)
        if entry is not None and self.clock() - entry[0] < max_age:
            return entry[1]
        return None

    def age(self, key: Hashable) -> float | None:
        entry = self._values.get(key)
        return None if entry is None else self.clock() - entry[0]

    def invalidate(self, key: Hashable) -> None:
        with self._guard:
            self._values.pop(key, None)

    def clear(self) -> None:
        with self._guard:
            self._values.clear()
            self._key_locks.clear()


# Process-wide caches shared by every DeltaClient (and therefore every tab and thread).
TICKER_CACHE = TtlCache()
PRODUCT_CACHE = TtlCache()


def clear_delta_caches() -> None:
    TICKER_CACHE.clear()
    PRODUCT_CACHE.clear()
