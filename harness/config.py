"""Runtime harness configuration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from logger import get_logger
from utils.env_util import env_bool, env_float, env_int, env_list, env_str

logger = get_logger("harness")

DEFAULT_GUARDRAILS = ["identity_privacy", "safety"]
SENSITIVE_OUTPUT_ACTIONS = {"redact", "block"}


@dataclass(frozen=True)
class HarnessConfig:
    recursion_limit: int = 25
    # idle_timeout: abort a chat stream only if no SSE event is emitted for
    # this many seconds. Active streams (tokens / tools / steps) extend
    # indefinitely — there is no wall-clock overall cap.
    idle_timeout: float = 60.0
    per_tool_timeout: float = 30.0
    max_tool_calls: int = 20
    max_tool_calls_per_task: int = 8
    max_skill_script_calls_per_task: int = 3
    max_parallel_tasks: int = 3
    max_tokens: int = 200_000
    llm_max_retries: int = 2
    tool_allowlist: list[str] | None = None  # None = allow all
    tool_denylist: list[str] = field(default_factory=list)
    enabled_guardrails: list[str] = field(default_factory=lambda: list(DEFAULT_GUARDRAILS))
    sensitive_output_scan: bool = True
    sensitive_output_action: str = "redact"
    # Short-term memory limits are separate from the aggregate per-request
    # provider budget controlled by ``max_tokens``.
    memory_max_tokens: int = 24_000
    memory_target_tokens: int = 12_000
    memory_keep_recent_turns: int = 4
    memory_ttl_seconds: float = 3_600.0

    def __post_init__(self) -> None:
        action = (self.sensitive_output_action or "redact").strip().lower()
        if action not in SENSITIVE_OUTPUT_ACTIONS:
            logger.warning("sensitive_output_action=%r is invalid, falling back to redact", self.sensitive_output_action)
            action = "redact"
        object.__setattr__(self, "sensitive_output_action", action)
        if self.memory_max_tokens <= 0:
            raise ValueError("memory_max_tokens must be positive")
        if self.memory_target_tokens <= 0 or self.memory_target_tokens >= self.memory_max_tokens:
            raise ValueError("memory_target_tokens must be positive and smaller than memory_max_tokens")
        if self.memory_keep_recent_turns <= 0:
            raise ValueError("memory_keep_recent_turns must be positive")
        if self.memory_ttl_seconds <= 0:
            raise ValueError("memory_ttl_seconds must be positive")

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        allow = env_list("TOOL_ALLOWLIST")
        guardrails = env_list("ENABLED_GUARDRAILS", DEFAULT_GUARDRAILS)
        return cls(
            recursion_limit=env_int("RECURSION_LIMIT", 25, logger),
            idle_timeout=env_float("IDLE_TIMEOUT_SECONDS", 60.0, logger),
            per_tool_timeout=env_float("PER_TOOL_TIMEOUT_SECONDS", 30.0, logger),
            max_tool_calls=env_int("MAX_TOOL_CALLS", 20, logger),
            max_tool_calls_per_task=env_int("MAX_TOOL_CALLS_PER_TASK", 8, logger),
            max_skill_script_calls_per_task=env_int("MAX_SKILL_SCRIPT_CALLS_PER_TASK", 3, logger),
            max_parallel_tasks=env_int("MAX_PARALLEL_TASKS", 3, logger),
            max_tokens=env_int("MAX_TOKENS_BUDGET", 200_000, logger),
            llm_max_retries=env_int("LLM_MAX_RETRIES", 2, logger),
            tool_allowlist=allow or None,
            tool_denylist=env_list("TOOL_DENYLIST"),
            enabled_guardrails=guardrails,
            sensitive_output_scan=env_bool("SENSITIVE_OUTPUT_SCAN", True, logger),
            sensitive_output_action=env_str("SENSITIVE_OUTPUT_ACTION", "redact"),
            memory_max_tokens=env_int("MEMORY_MAX_TOKENS", 24_000, logger),
            memory_target_tokens=env_int("MEMORY_TARGET_TOKENS", 12_000, logger),
            memory_keep_recent_turns=env_int("MEMORY_KEEP_RECENT_TURNS", 4, logger),
            memory_ttl_seconds=env_float("MEMORY_TTL_SECONDS", 3_600.0, logger),
        )

    def merge(self, overrides: dict[str, Any]) -> "HarnessConfig":
        """Return a new config with non-None values from `overrides` applied."""
        clean = {k: v for k, v in overrides.items() if v is not None and hasattr(self, k)}
        return replace(self, **clean)
