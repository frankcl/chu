"""Extract compact source metadata from tool outputs for UI rendering."""

from __future__ import annotations

import json
import re
from typing import Any


def _add(out: dict[str, str], url: Any, favicon: Any) -> None:
    if isinstance(url, str) and isinstance(favicon, str) and url.strip() and favicon.strip():
        out[url.strip()] = favicon.strip()


def _walk(value: Any, out: dict[str, str]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk(item, out)
        return
    if not isinstance(value, dict):
        return
    _add(out, value.get("url"), value.get("favicon"))
    for item in value.values():
        _walk(item, out)


def _decode_json_string(value: str) -> str:
    try:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return json.loads(f'"{escaped}"')
    except Exception:
        return value


def extract_source_favicons(text: str, limit: int = 50) -> list[dict[str, str]]:
    """Return [{url, favicon}] found in a Tavily-style tool result."""
    raw = str(text or "")
    found: dict[str, str] = {}

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            _walk(json.loads(raw[start:end + 1]), found)
        except Exception:
            pass

    patterns = (
        re.compile(r'"url"\s*:\s*"([^"]+)"[\s\S]{0,500}?"favicon"\s*:\s*"([^"]+)"'),
        re.compile(r'"favicon"\s*:\s*"([^"]+)"[\s\S]{0,500}?"url"\s*:\s*"([^"]+)"'),
    )
    for pattern in patterns:
        for match in pattern.finditer(raw):
            first = _decode_json_string(match.group(1))
            second = _decode_json_string(match.group(2))
            if pattern.pattern.startswith('"url"'):
                _add(found, first, second)
            else:
                _add(found, second, first)
            if len(found) >= limit:
                break

    return [{"url": url, "favicon": favicon} for url, favicon in list(found.items())[:limit]]


def _is_web_research_search_input(tool_input: Any) -> bool:
    if isinstance(tool_input, dict):
        return (
            str(tool_input.get("skill") or "") == "web-research"
            and str(tool_input.get("script") or "") == "search.py"
        )
    raw = str(tool_input or "")
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _is_web_research_search_input(parsed)
    except Exception:
        pass
    return bool(
        re.search(r'["\']?skill["\']?\s*[:=]\s*["\']web-research["\']', raw)
        and re.search(r'["\']?script["\']?\s*[:=]\s*["\']search\.py["\']', raw)
    )


def source_favicons_for_tool(
    tool_name: Any,
    result: str,
    tool_input: Any = None,
    limit: int = 50,
) -> list[dict[str, str]]:
    """Return source favicons only for the web search script result."""
    name = str(tool_name or "")
    if name == "search" or (name == "run_skill_script" and _is_web_research_search_input(tool_input)):
        return extract_source_favicons(result, limit=limit)
    return []
