from __future__ import annotations

import logging

from delta_intelligence.utils.logging_utils import MASK, get_logger, log_event, redact, register_secret


def test_registered_secret_is_masked() -> None:
    register_secret("supersecretvalue123")
    assert "supersecretvalue123" not in redact("calling with supersecretvalue123 now")


def test_key_value_patterns_are_masked() -> None:
    text = 'headers={"api-key": "abcdef123", "signature": "deadbeefcafe", "timestamp": "1"} password=hunter22'
    out = redact(text)
    for secret in ("abcdef123", "deadbeefcafe", "hunter22"):
        assert secret not in out
    assert '"timestamp": "1"' in out and MASK in out


def test_ordinary_text_untouched() -> None:
    assert redact("expired_signature for BTCUSD at 05:30 IST") == "expired_signature for BTCUSD at 05:30 IST"


def test_log_event_writes_redacted_output() -> None:
    register_secret("LEAKY-SECRET-999")
    log_event("test", "using LEAKY-SECRET-999", api_secret="LEAKY-SECRET-999", detail="signature=abc123xyz")
    for h in get_logger().handlers:
        h.flush()
    files = [h.baseFilename for h in get_logger().handlers if isinstance(h, logging.FileHandler)]
    assert files, "file handler expected"
    content = open(files[0], encoding="utf-8").read()
    assert "LEAKY-SECRET-999" not in content and "abc123xyz" not in content
    assert "using ***" in content
