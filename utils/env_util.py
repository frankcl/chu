"""Typed environment-variable helpers."""

from __future__ import annotations

import os
from typing import Any


def env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else raw


def env_int(name: str, default: int, logger: Any | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        if logger is not None:
            logger.warning("env %s=%r is not an int, falling back to %s", name, raw, default)
        return default


def env_float(name: str, default: float, logger: Any | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        if logger is not None:
            logger.warning("env %s=%r is not a float, falling back to %s", name, raw, default)
        return default


def env_bool(name: str, default: bool, logger: Any | None = None) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    if logger is not None:
        logger.warning("env %s=%r is not a bool, falling back to %s", name, raw, default)
    return default


def env_list(name: str, default: list[str] | None = None, sep: str = ",") -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return list(default or [])
    return [item.strip() for item in raw.split(sep) if item.strip()]
