"""Tests for sensitive output scanning and redaction."""

from harness import HarnessConfig
from harness.output_guard import (
    SENSITIVE_OUTPUT_BLOCK_MESSAGE,
    guard_output_item,
    redact_sensitive_text,
    scan_sensitive_text,
)


def test_scan_sensitive_text_detects_secret_patterns():
    text = (
        "Authorization: Bearer abcdefghijklmnop\n"
        "DATABASE_URL=mysql+pymysql://u:p@localhost:3306/db\n"
        "Set-Cookie: sid=secret\n"
        "Traceback (most recent call last):\n  File \"/Users/frankcl/app.py\", line 1\n"
        "OPENAI_API_KEY=sk-abcdefghijklmnop\n"
    )
    kinds = {finding.kind for finding in scan_sensitive_text(text)}
    assert {"bearer_token", "database_url", "cookie", "traceback", "api_key"} <= kinds


def test_redact_sensitive_text_replaces_matches():
    text = "token Bearer abcdefghijklmnop at /Users/frankcl/project/server.py"
    out = redact_sensitive_text(text)
    assert "abcdefghijklmnop" not in out
    assert "/Users/frankcl" not in out
    assert "[REDACTED:bearer_token]" in out
    assert "[REDACTED:server_path]" in out


def test_guard_output_item_can_be_disabled():
    cfg = HarnessConfig(sensitive_output_scan=False)
    item = {"type": "text", "content": "Bearer abcdefghijklmnop"}
    guarded, reason = guard_output_item(item, cfg)
    assert guarded is item
    assert reason is None


def test_guard_output_item_redacts_visible_fields():
    cfg = HarnessConfig(sensitive_output_action="redact")
    item = {"type": "tool", "result": "DATABASE_URL=mysql://u:p@localhost/db", "name": "tool"}
    guarded, reason = guard_output_item(item, cfg)
    assert reason is None
    assert guarded["name"] == "tool"
    assert guarded["result"] == "[REDACTED:env_assignment]"


def test_guard_output_item_blocks_visible_fields():
    cfg = HarnessConfig(sensitive_output_action="block")
    item = {"type": "text", "content": "Cookie: sid=secret"}
    guarded, reason = guard_output_item(item, cfg)
    assert reason == "sensitive_output"
    assert guarded == {
        "type": "limit",
        "reason": "sensitive_output",
        "message": SENSITIVE_OUTPUT_BLOCK_MESSAGE,
    }
