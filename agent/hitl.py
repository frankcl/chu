"""人工介入（Human-in-the-Loop, HITL）模块。

提供一个通用的"暂停 → 等待人工选择 → 带着选择继续"原语，可被任意 skill / 流程复用。
首个使用方是 ppt skill：制作 PPT 前让用户选择模板风格（见 skills/ppt/SKILL.md）。

设计要点（为何不用 LangGraph 原生 `interrupt()`）：plan-execute 模式下每步的执行器是
**手动调用**的、无 checkpointer 的内层 ReAct 图（plan_execute_agent.py），`interrupt()`
在 resume 时会重跑整个外层节点，与已经跑过的 `executor.astream()` 冲突，难以正确续跑。

这里改用一个**每会话的异步暂停/恢复通道** `HitlChannel`：HITL 工具向 SSE 流发出一个
`hitl` 事件，然后 `await` 用户的回答（由 `/respond` 端点解析 future）。无需 checkpointer、
不挂起图，两种模式行为一致，并复用既有的 producer→asyncio.Queue→SSE 管道。
"""

import asyncio
import uuid
from typing import Callable

from langchain_core.tools import BaseTool, StructuredTool

from .log import get_logger

logger = get_logger("hitl")


class HitlChannel:
    """每会话的人工介入通道：发出问询事件并等待回答。

    单会话同一时刻只会有一个进行中的请求（每会话仅一个活动任务）。`emit` 在每次请求
    （chat handler）开始时通过 `bind_emit` 绑定到当次的 SSE 队列。
    """

    def __init__(self):
        self._emit: Callable[[dict], None] | None = None
        self._pending_id: str | None = None
        self._future: asyncio.Future | None = None

    def bind_emit(self, emit: Callable[[dict], None]) -> None:
        """绑定当次请求的事件发射器（通常是 queue.put_nowait）。"""
        self._emit = emit

    def is_pending(self) -> bool:
        """是否有未完成的人工问询（用于让流在等待人工时跳过 idle 超时）。"""
        return self._future is not None and not self._future.done()

    async def request(self, prompt: str, options: list[str], preview: str | None = None) -> str:
        """发出一个人工选择问询，阻塞到 `respond()` 给出答案，返回所选项。

        `preview` 为可选的预览类型提示（如 "ppt-theme"），原样带入 SSE 事件，前端据此
        渲染更丰富的预览（如模板缩略图）；为 None 时前端回退到普通选项按钮。
        """
        if self._emit is None:
            # 无 SSE 通道（如 CLI / 测试直接调用）时优雅降级：默认取第一个选项。
            logger.warning("hitl request with no emit bound; defaulting to first option")
            return options[0] if options else ""
        rid = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._pending_id = rid
        logger.info("hitl request id=%s prompt=%.60s options=%s preview=%s",
                    rid, prompt, options, preview)
        self._emit({"type": "hitl", "id": rid, "prompt": prompt,
                    "options": options, "preview": preview})
        try:
            value = await self._future
        finally:
            self._pending_id = None
            self._future = None
        logger.info("hitl resolved id=%s value=%r", rid, value)
        return value

    def respond(self, rid: str, value: str) -> bool:
        """用人工回答解析对应的未完成 future；id 不匹配或已完成则忽略。"""
        if self._future is None or self._future.done():
            return False
        if rid != self._pending_id:
            logger.info("hitl respond ignored stale id=%s (pending=%s)", rid, self._pending_id)
            return False
        self._future.set_result(value)
        return True

    def cancel(self) -> None:
        """取消未完成的请求（会话删除 / 取消时调用），唤醒被阻塞的工具。"""
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._pending_id = None
        self._future = None


class ConsoleHitlChannel:
    """CLI 版人工介入：直接在终端打印问题并阻塞读取用户输入。

    与 `HitlChannel`（Web/SSE）实现相同的 `request` 接口，外加一个供同步 `.stream()`
    使用的 `request_sync`，所以同一个 `request_user_choice` 工具在 CLI 与服务端都能用。
    """

    def _ask(self, prompt: str, options: list[str]) -> str:
        print(f"\n[请选择] {prompt}", flush=True)
        for i, o in enumerate(options, 1):
            print(f"  {i}. {o}", flush=True)
        if not options:
            return input("> ").strip()
        while True:
            raw = input("输入序号或选项: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            if raw in options:
                return raw
            print("无效输入，请重试。", flush=True)

    def request_sync(self, prompt: str, options: list[str], preview: str | None = None) -> str:
        return self._ask(prompt, options)  # preview is a UI hint; ignored on the CLI

    async def request(self, prompt: str, options: list[str], preview: str | None = None) -> str:
        return self._ask(prompt, options)


def make_request_user_choice_tool(channel) -> BaseTool:
    """构造绑定到某会话通道的 `request_user_choice` 工具。

    同时提供异步（服务端 astream）与同步（CLI .stream）两条路径，分别委托给通道的
    `request` / `request_sync`。

    注意：该工具**不能**被 harness 的 `_TimeoutTool` 包裹——后者在独立线程里跑工具，会
    破坏 await 语义且无法支持不定时长的人工等待。调用方需在 `wrap_tools` 之后单独追加它。
    """

    async def _arequest_user_choice(
        prompt: str, options: list[str], preview_kind: str | None = None
    ) -> str:
        return await channel.request(prompt, options, preview=preview_kind)

    def _request_user_choice(
        prompt: str, options: list[str], preview_kind: str | None = None
    ) -> str:
        request_sync = getattr(channel, "request_sync", None)
        if request_sync is None:
            raise NotImplementedError("request_user_choice requires an async runtime")
        return request_sync(prompt, options, preview=preview_kind)

    return StructuredTool.from_function(
        func=_request_user_choice,
        coroutine=_arequest_user_choice,
        name="request_user_choice",
        description=(
            "Ask the human user to make a choice and PAUSE until they answer. Use this "
            "whenever a decision should be made by the user rather than by you — e.g. "
            "picking a style/template, confirming a destructive action, or choosing among "
            "options. Args: `prompt` (the question, in the user's language), `options` "
            "(a list of allowed choices), and optional `preview_kind` (a hint that makes the "
            "UI render richer previews for the options — pass \"ppt-theme\" when the options "
            "are PPT template themes). Returns the option string the user selected; use it "
            "verbatim. Do not call this for things you can decide yourself."
        ),
    )
