"""
Live price feed over Delta's PUBLIC WebSocket (verified 2026-10-02; docs/DELTA_API_NOTES.md section 10).

- URL `wss://public-socket.india.delta.exchange` (testnet: `wss://socket-ind-pub.testnet.deltaex.org`).
- Channel `ticker`, in a compact format: `q` = [ask, ask_size, bid, bid_size, ...], `qiv` = [ask_iv, bid_iv, mark_iv],
  `m` = mark, `sp` = spot, `ts` = µs.
- `{"type":"enable_heartbeat"}` after every connect. If no message (heartbeat or data) arrives within
  `HEARTBEAT_TIMEOUT` seconds the connection is dropped and re-established.
- Reconnects back off exponentially (2 s → 60 s) and are capped to stay well inside Delta's limit of 150 connections
  per 5 minutes per IP.

It runs in its own daemon thread and never touches Streamlit. Readers call `quote(symbol)` / `snapshot()`, which are
thread-safe. REST stays the source of truth for bootstrap and reconciliation; this feed only keeps prices fresh.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from delta_intelligence.utils.logging_utils import log_event

HEARTBEAT_TIMEOUT = 35.0
MAX_CONNECTS_PER_5MIN = 30  # far below Delta's 150


@dataclass
class LiveQuote:
    symbol: str
    bid: float | None
    ask: float | None
    mark: float | None
    spot: float | None
    mark_iv: float | None
    ts_us: int | None
    received: float  # local monotonic time


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_message(msg: dict, now: float) -> list[LiveQuote]:
    """Compact public `ticker` messages and full `v2/ticker` messages → LiveQuotes. Anything else → []."""
    t = msg.get("type")
    if t == "ticker" and isinstance(msg.get("d"), list):
        out = []
        for d in msg["d"]:
            q = d.get("q") or [None] * 5
            qiv = d.get("qiv") or [None] * 3
            out.append(LiveQuote(d.get("s") or msg.get("sy"), _f(q[2]), _f(q[0]), _f(d.get("m")), _f(msg.get("sp")),
                                 _f(qiv[2]) if len(qiv) > 2 else None, msg.get("ts"), now))
        return out
    if t == "v2/ticker" and msg.get("symbol"):
        q = msg.get("quotes") or {}
        return [LiveQuote(msg["symbol"], _f(q.get("best_bid")), _f(q.get("best_ask")), _f(msg.get("mark_price")),
                          _f(msg.get("spot_price")), _f(q.get("mark_iv")), msg.get("timestamp"), now)]
    return []


class TickerFeed:
    def __init__(self, url: str, symbols: list[str] | None = None, connect: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic, user_agent: str = "delta-intelligence"):
        self.url = url
        self._symbols = set(symbols or [])
        self._connect = connect  # injectable for tests: returns an object with send/recv/close/settimeout
        self.clock = clock
        self.user_agent = user_agent
        self._quotes: dict[str, LiveQuote] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws = None
        self._resubscribe = threading.Event()
        self.connects: deque[float] = deque(maxlen=200)
        self.last_message: float | None = None
        self.reconnects = 0
        self.status = "stopped"

    # ---- public API ------------------------------------------------------------------------------------------------
    def set_symbols(self, symbols) -> None:
        with self._lock:
            new = set(symbols)
            changed = new != self._symbols
            self._symbols = new
        if changed:
            self._resubscribe.set()

    def quote(self, symbol: str, max_age: float | None = None) -> LiveQuote | None:
        with self._lock:
            q = self._quotes.get(symbol)
        if q is None or (max_age is not None and self.clock() - q.received > max_age):
            return None
        return q

    def snapshot(self) -> dict[str, LiveQuote]:
        with self._lock:
            return dict(self._quotes)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ws-ticker-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- internals ---------------------------------------------------------------------------------------------------
    def _open(self):
        if self._connect is not None:
            return self._connect(self.url)
        import websocket

        return websocket.create_connection(self.url, timeout=10, header=[f"User-Agent: {self.user_agent}"])

    def _subscribe(self, ws) -> None:
        with self._lock:
            syms = sorted(self._symbols)
        if syms:
            ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [{"name": "ticker", "symbols": syms}]}}))
        ws.send(json.dumps({"type": "enable_heartbeat"}))

    def _can_connect(self) -> bool:
        now = self.clock()
        while self.connects and now - self.connects[0] > 300:
            self.connects.popleft()
        return len(self.connects) < MAX_CONNECTS_PER_5MIN

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            if not self._can_connect():
                self.status = "throttled"
                self._stop.wait(30)
                continue
            try:
                self.status = "connecting"
                self.connects.append(self.clock())
                ws = self._open()
                self._ws = ws
                ws.settimeout(5)
                self._subscribe(ws)
                self.status = "connected"
                self.last_message = self.clock()
                backoff = 2.0
                while not self._stop.is_set():
                    if self._resubscribe.is_set():
                        self._resubscribe.clear()
                        self._subscribe(ws)
                    try:
                        raw = ws.recv()
                    except Exception as exc:  # timeout or drop
                        if type(exc).__name__ in ("WebSocketTimeoutException", "TimeoutError", "timeout"):
                            raw = None
                        else:
                            raise
                    now = self.clock()
                    if raw:
                        self.last_message = now
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        quotes = parse_message(msg, now)
                        if quotes:
                            with self._lock:
                                for q in quotes:
                                    if q.symbol:
                                        self._quotes[q.symbol] = q
                    if self.last_message is not None and now - self.last_message > HEARTBEAT_TIMEOUT:
                        raise ConnectionError("no heartbeat/data within 35 s")
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.reconnects += 1
                self.status = "reconnecting"
                log_event("ws_feed", f"websocket dropped ({type(exc).__name__}); reconnecting in {backoff:.0f}s",
                          level="WARNING")
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        self.status = "stopped"
