"""Harness controls for the Agent runtime.

Centralizes runtime safety limits so failures fail fast and observably:
  - HarnessConfig: env-defaulted, per-session overridable knobs
  - BudgetTracker: a LangChain callback that aborts on token / tool-call budget
  - wrap_tools: filters by allow/deny list and enforces per-tool timeouts
  - apply_llm_retry: exponential-backoff retry on transient provider errors

`recursion_limit` is not enforced here — it is passed through to
LangGraph at astream() time via config={"recursion_limit": ...}.
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .log import get_logger

logger = get_logger("harness")


# ── exceptions ──────────────────────────────────────────────────────────────

class BudgetExceededError(RuntimeError):
    """Raised by BudgetTracker when a budget threshold is crossed."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # short machine-readable tag, e.g. "token" / "tool_calls"
        self.message = message


class TaskBudgetExceededError(RuntimeError):
    """Raised when a single plan task exceeds its local runtime budget."""

    def __init__(self, task_id: str, reason: str, message: str):
        super().__init__(message)
        self.task_id = task_id
        self.reason = reason
        self.message = message


# ── config ──────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("env %s=%r is not an int, falling back to %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("env %s=%r is not a float, falling back to %s", name, raw, default)
        return default


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


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

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        allow = _env_list("TOOL_ALLOWLIST")
        return cls(
            recursion_limit=_env_int("RECURSION_LIMIT", 25),
            idle_timeout=_env_float("IDLE_TIMEOUT_SECONDS", 60.0),
            per_tool_timeout=_env_float("PER_TOOL_TIMEOUT_SECONDS", 30.0),
            max_tool_calls=_env_int("MAX_TOOL_CALLS", 20),
            max_tool_calls_per_task=_env_int("MAX_TOOL_CALLS_PER_TASK", 8),
            max_skill_script_calls_per_task=_env_int("MAX_SKILL_SCRIPT_CALLS_PER_TASK", 3),
            max_parallel_tasks=_env_int("MAX_PARALLEL_TASKS", 3),
            max_tokens=_env_int("MAX_TOKENS_BUDGET", 200_000),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 2),
            tool_allowlist=allow or None,
            tool_denylist=_env_list("TOOL_DENYLIST"),
        )

    def merge(self, overrides: dict[str, Any]) -> "HarnessConfig":
        """Return a new config with non-None values from `overrides` applied."""
        clean = {k: v for k, v in overrides.items() if v is not None and hasattr(self, k)}
        return replace(self, **clean)


# ── budget tracker ──────────────────────────────────────────────────────────

class BudgetTracker(BaseCallbackHandler):
    """Per-request token + tool-call budget.

    Attach via config={"callbacks": [tracker]}. One instance per astream call.
    Raising inside a callback propagates up through LangGraph and is caught
    by the server's stream wrapper to emit a `limit` SSE event.
    """

    raise_error = True  # let exceptions bubble up rather than be swallowed

    def __init__(self, cfg: HarnessConfig):
        self.cfg = cfg
        self.total_tokens = 0
        # 输入/输出 token 细分（用于 session 级消耗统计；预算判断仍只看 total）。
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0

    def on_llm_end(self, response, **kwargs):  # type: ignore[override]
        # response.llm_output may carry token_usage (OpenAI/Qwen path).
        # response.generations[0][0].message.usage_metadata is the unified path (langchain-core).
        used = inp = outp = 0
        try:
            gens = response.generations or []
            if gens and gens[0]:
                msg = getattr(gens[0][0], "message", None)
                meta = getattr(msg, "usage_metadata", None) if msg else None
                if meta:
                    used = int(meta.get("total_tokens") or 0)
                    inp = int(meta.get("input_tokens") or 0)
                    outp = int(meta.get("output_tokens") or 0)
            if response.llm_output:
                tu = response.llm_output.get("token_usage") or {}
                used = used or int(tu.get("total_tokens") or 0)
                inp = inp or int(tu.get("prompt_tokens") or 0)
                outp = outp or int(tu.get("completion_tokens") or 0)
        except Exception:
            used = inp = outp = 0
        if not used:
            used = inp + outp  # total 缺失时用 input+output 兜底
        self.total_tokens += used
        self.input_tokens += inp
        self.output_tokens += outp
        if self.cfg.max_tokens and self.total_tokens > self.cfg.max_tokens:
            raise BudgetExceededError(
                "token",
                f"token budget exceeded: {self.total_tokens} > {self.cfg.max_tokens}",
            )

    def on_tool_start(self, serialized, input_str, **kwargs):  # type: ignore[override]
        self.tool_calls += 1
        if self.cfg.max_tool_calls and self.tool_calls > self.cfg.max_tool_calls:
            raise BudgetExceededError(
                "tool_calls",
                f"tool-call budget exceeded: {self.tool_calls} > {self.cfg.max_tool_calls}",
            )


class TaskBudgetTracker(BaseCallbackHandler):
    """Per-plan-task tool-call budget.

    Attach this only to the inner executor run for one plan task. A budget hit
    should fail that task, not the whole user turn; the plan executor catches
    TaskBudgetExceededError and summarizes partial results.
    """

    raise_error = True

    def __init__(self, task_id: str, cfg: HarnessConfig):
        self.task_id = task_id
        self.cfg = cfg
        self.tool_calls = 0

    def on_tool_start(self, serialized, input_str, **kwargs):  # type: ignore[override]
        self.tool_calls += 1
        limit = self.cfg.max_tool_calls_per_task
        if limit and self.tool_calls > limit:
            raise TaskBudgetExceededError(
                self.task_id,
                "tool_calls_per_task",
                (
                    f"task {self.task_id!r} tool-call budget exceeded: "
                    f"{self.tool_calls} > {limit}"
                ),
            )


# ── tool wrapping (filter + timeout) ────────────────────────────────────────

class _TimeoutTool(BaseTool):
    """Wraps a BaseTool so any single invocation must finish within `timeout`.

    On timeout we return a string (not raise) so the Agent observes a
    failure result and can decide what to do next, rather than crashing
    the whole graph run.

    Implementation note: we deliberately avoid a process-wide
    ThreadPoolExecutor. Pools pre-reserve N thread stacks, hold worker
    threads alive past test/process exit (atexit join), and tie up memory
    even when idle. A per-call daemon thread costs ~one stack-frame for
    the lifetime of the call and dies with the process.
    """

    inner: BaseTool
    timeout: float

    # pydantic config: allow arbitrary types (BaseTool)
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, inner: BaseTool, timeout: float, **kwargs):
        super().__init__(
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            inner=inner,
            timeout=timeout,
            **kwargs,
        )

    def _run(self, *args, **kwargs):
        payload = kwargs if kwargs else (args[0] if args else {})
        box: dict = {"result": None, "exc": None}

        def worker():
            try:
                box["result"] = self.inner.invoke(payload)
            except BaseException as e:  # noqa: BLE001 — propagated below
                box["exc"] = e

        t = threading.Thread(target=worker, name=f"harness-tool-{self.name}", daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            logger.warning("tool=%s timed out after %.1fs", self.name, self.timeout)
            return f"[tool timeout: {self.name} exceeded {self.timeout:g}s]"
        if box["exc"] is not None:
            raise box["exc"]
        return box["result"]

    async def _arun(self, *args, **kwargs):
        payload = kwargs if kwargs else (args[0] if args else {})
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.inner.invoke, payload),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("tool=%s timed out after %.1fs", self.name, self.timeout)
            return f"[tool timeout: {self.name} exceeded {self.timeout:g}s]"


def wrap_tools(tools: list[BaseTool], cfg: HarnessConfig) -> list[BaseTool]:
    """Filter by allow/deny list, then wrap each tool with a timeout guard."""
    deny = set(cfg.tool_denylist or [])
    allow = set(cfg.tool_allowlist) if cfg.tool_allowlist else None

    out: list[BaseTool] = []
    for t in tools:
        if t.name in deny:
            continue
        if allow is not None and t.name not in allow:
            continue
        out.append(_TimeoutTool(t, cfg.per_tool_timeout))
    return out


# ── LLM retry ───────────────────────────────────────────────────────────────

def _retryable_exception_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:
        import anthropic  # type: ignore
        types += [anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError]
    except Exception:
        pass
    try:
        import openai  # type: ignore
        types += [openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError]
    except Exception:
        pass
    try:
        import httpx  # type: ignore
        types += [httpx.TimeoutException, httpx.ConnectError]
    except Exception:
        pass
    return tuple(types) if types else (Exception,)


def apply_llm_retry(llm: BaseChatModel, cfg: HarnessConfig) -> BaseChatModel:
    """Wrap an LLM with exponential-backoff retry on transient provider errors."""
    if cfg.llm_max_retries <= 0:
        return llm
    exc_types = _retryable_exception_types()
    return llm.with_retry(
        retry_if_exception_type=exc_types,
        stop_after_attempt=cfg.llm_max_retries + 1,
        wait_exponential_jitter=True,
    )
