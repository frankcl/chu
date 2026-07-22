"""Token and tool-call budget guardrails."""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

from .config import HarnessConfig


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


class BudgetTracker(BaseCallbackHandler):
    """Per-request token + tool-call budget."""

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
    """Per-plan-task tool-call budget."""

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
