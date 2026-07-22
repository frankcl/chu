"""Sensitive output scanning and redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import HarnessConfig

SENSITIVE_OUTPUT_BLOCK_MESSAGE = "检测到敏感输出，已阻止展示。"


@dataclass(frozen=True)
class Finding:
    kind: str
    value: str


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("api_key", re.compile(r"\b(?:sk|ak)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE)),
    ("cloud_access_key", re.compile(r"\b(?:AKIA|ASIA|LTAI)[A-Z0-9]{12,}\b")),
    ("cookie", re.compile(r"\b(?:set-cookie|cookie)\s*:\s*[^;\n]+", re.IGNORECASE)),
    ("database_url", re.compile(r"\b(?:mysql|postgresql|postgres|sqlite)(?:\+\w+)?://[^\s'\"<>]+", re.IGNORECASE)),
    ("env_assignment", re.compile(r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s'\"\n]{8,}")),
    ("traceback", re.compile(r"Traceback \(most recent call last\):[\s\S]{0,1200}", re.IGNORECASE)),
    ("stack_trace", re.compile(r"\b(?:File \"[^\"]+\", line \d+|stack trace|exception traceback)\b", re.IGNORECASE)),
    ("server_path", re.compile(r"(?<!\w)(?:/Users/|/private/|/var/|/etc/|/home/)[^\s'\"<>:]{3,}")),
]


def scan_sensitive_text(text: str) -> list[Finding]:
    findings: list[Finding] = []
    if not text:
        return findings
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Finding(kind=kind, value=match.group(0)))
    return findings


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for kind, pattern in _PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
    return redacted


def _visible_text_fields(item: dict[str, Any]) -> list[str]:
    keys = ["content", "result", "text", "message"]
    item_type = item.get("type")
    if item_type in {"tool_start", "step_tool_start"}:
        keys.append("input")
    return [key for key in keys if isinstance(item.get(key), str)]


def guard_output_item(item: dict[str, Any], cfg: HarnessConfig) -> tuple[dict[str, Any], str | None]:
    if not cfg.sensitive_output_scan:
        return item, None
    fields = _visible_text_fields(item)
    if not fields:
        return item, None
    findings = []
    for field in fields:
        findings.extend(scan_sensitive_text(item[field]))
    if not findings:
        return item, None
    if cfg.sensitive_output_action == "block":
        return {
            "type": "limit",
            "reason": "sensitive_output",
            "message": SENSITIVE_OUTPUT_BLOCK_MESSAGE,
        }, "sensitive_output"
    redacted = dict(item)
    for field in fields:
        redacted[field] = redact_sensitive_text(redacted[field])
    return redacted, None
