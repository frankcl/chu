"""Tool filtering and timeout wrappers."""

from __future__ import annotations

import asyncio
import threading

from langchain_core.tools import BaseTool

from logger import get_logger

from .config import HarnessConfig

logger = get_logger("harness")


class _TimeoutTool(BaseTool):
    """Wraps a BaseTool so any single invocation must finish within `timeout`."""

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
    for tool in tools:
        if tool.name in deny:
            continue
        if allow is not None and tool.name not in allow:
            continue
        out.append(_TimeoutTool(tool, cfg.per_tool_timeout))
    return out
