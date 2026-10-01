"""
Structured logging with secret redaction, plus decision IDs.

Events go to a rotating JSON-lines file (`logs/system.log`), the console and, once the database exists (P4), the
`system_events` table. Logging must never crash the caller.

**Secrets never reach a log.** Every message and detail passes through `redact()`, which:
- masks every value registered with `register_secret()` (the API key and secret are registered when a client is
  built), and
- masks anything shaped like `api-key=...`, `"signature": "..."`, `secret: ...` or `password=...`.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from logging.handlers import RotatingFileHandler
from typing import Any

_LOGGER_NAME = "delta_intelligence"
MASK = "***"

_secrets: set[str] = set()
_secrets_lock = threading.Lock()

_FIELD_RE = re.compile(
    r"""(?ix)
    (["']?(?:api[-_]?key|api[-_]?secret|secret|signature|password|authorization)["']?\s*[:=]\s*["']?)
    ([^"'\s,;}&]+)
    """
)


def register_secret(value: str | None) -> None:
    """Mask this exact value wherever it appears in log output. Short values are ignored (too many false hits)."""
    if value and len(value) >= 6:
        with _secrets_lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _secrets_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        text = text.replace(s, MASK)
    return _FIELD_RE.sub(lambda m: m.group(1) + MASK, text)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: (MASK if _FIELD_RE.match(f"{k}=x") else _redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v) for v in obj]
    return obj


def new_decision_id(prefix: str = "DEC") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(_redact_obj(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter('{"ts": "%(asctime)s", "level": "%(levelname)s", "component": "%(name)s", "msg": %(message)s}')
    redactor = _RedactingFilter()

    try:
        from delta_intelligence.config.settings import get_settings

        logs_dir = get_settings().logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(logs_dir / "system.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(redactor)
        logger.addHandler(fh)
    except Exception:
        pass  # read-only filesystem etc.: console logging still works

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(redactor)
    logger.addHandler(sh)
    return logger


def log_event(component: str, message: str, level: str = "INFO", **details: Any) -> None:
    """Log a structured, redacted event, and persist it to `system_events` when the database is available."""
    message = redact(message)
    details = _redact_obj(details)
    try:
        payload = json.dumps({"message": message, "details": details}, default=str)
        logger = get_logger().getChild(component)
        getattr(logger, level.lower(), logger.info)(payload)
    except Exception:
        pass
    try:
        from delta_intelligence.database.db import persist_system_event  # available from P4

        persist_system_event(component=component, level=level, message=message, details=details or None)
    except Exception:
        pass
