"""Chat streaming, cancellation, and HITL endpoints."""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent import HitlChannel, iter_chunk_outputs
from agent.source_meta import source_favicons_for_tool
from harness import (
    BudgetExceededError,
    BudgetTracker,
    HarnessConfig,
    evaluate_input_guardrails,
    guard_output_item,
)
from logger import get_logger
from memory import MemoryManager
import storage as db

from .runtime import sessions

logger = get_logger("web_api.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class RespondRequest(BaseModel):
    id: str
    value: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_content_moderation_error(exc: Exception) -> bool:
    """Detect provider-side content-moderation rejections.

    Aliyun DashScope (Qwen) enforces output content inspection on the platform
    side — for sensitive topics (e.g. stock/financial advice in China) it aborts
    the stream with an APIError carrying code `data_inspection_failed` / a message
    about "inappropriate content". This is a policy block, not a transient fault,
    so we surface it as a clean `limit` event instead of a raw error.
    """
    code = str(getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    return (
        "data_inspection_failed" in code
        or "data_inspection_failed" in msg
        or "inappropriate content" in msg
    )


class _SseToolStartCallback(BaseCallbackHandler):
    """Emit a generic SSE tool_start event for ReAct mode tool calls."""

    def __init__(self, emit):
        self.emit = emit
        self.inputs_by_call_id: dict[str, str] = {}

    def on_tool_start(self, serialized, input_str, **kwargs):  # type: ignore[override]
        name = ""
        if isinstance(serialized, dict):
            name = serialized.get("name") or serialized.get("id") or ""
        name = str(name or "tool")
        tool_input = str(input_str or "")
        tool_call_id = str(kwargs.get("tool_call_id") or kwargs.get("run_id") or "")
        if tool_call_id:
            self.inputs_by_call_id[tool_call_id] = tool_input
        self.emit({
            "type": "tool_start",
            "name": name,
            "input": tool_input[:500],
        })

    def finish(self, tool_call_id: str | None) -> str:
        if not tool_call_id:
            return ""
        return self.inputs_by_call_id.pop(str(tool_call_id), "")



@router.post("/{session_id}/cancel")
def cancel_chat(session_id: str):
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    s["last_access_at"] = time.monotonic()
    if s.get("hitl"):
        s["hitl"].cancel()
    task = s.get("active_task")
    if task and not task.done():
        task.cancel()
        logger.info("cancel requested session=%s", session_id)
        return {"ok": True, "cancelled": True}
    return {"ok": True, "cancelled": False}


@router.post("/{session_id}/respond")
def respond_chat(session_id: str, body: RespondRequest):
    """Deliver a human's answer to a pending HITL request; the open SSE stream resumes."""
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    s["last_access_at"] = time.monotonic()
    hitl: HitlChannel = s["hitl"]
    ok = hitl.respond(body.id, body.value)
    logger.info("hitl respond session=%s id=%s ok=%s", session_id, body.id, ok)
    return {"ok": ok}


class _TurnRecorder:
    """累积一轮对话的 AI 全量产出，落库时转成 chat_message 行（全量保真）。

    观察流中已入队的 typed item（同 SSE 下发的内容），按类型聚合：文本、思考、
    工具调用、plan、plan-execute 各步骤。
    """

    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.tools: list[dict] = []          # {name, result}
        self.plan: list = []
        self.steps: dict[int, dict] = {}     # step_num -> {task, text, thinking, tools}

    def _step(self, num: int) -> dict:
        return self.steps.setdefault(num, {"task": "", "text": "", "thinking": "", "tools": []})

    def observe(self, item: dict) -> None:
        t = item.get("type")
        if t == "text":
            self.text += item.get("content", "")
        elif t == "thinking":
            self.thinking += item.get("content", "")
        elif t == "tool":
            self.tools.append({
                "name": item.get("name"),
                "result": item.get("result", ""),
                "source_favicons": item.get("source_favicons") or [],
            })
        elif t == "plan":
            self.plan = item.get("steps", [])
        elif t == "step_start":
            self._step(item["step_num"])["task"] = item.get("task", "")
        elif t == "step_token":
            self._step(item["step_num"])["text"] += item.get("text", "")
        elif t == "step_thinking":
            self._step(item["step_num"])["thinking"] += item.get("text", "")
        elif t == "step_tool":
            self._step(item["step_num"])["tools"].append(
                {
                    "name": item.get("name"),
                    "result": item.get("result", ""),
                    "source_favicons": item.get("source_favicons") or [],
                }
            )

    def rows(self) -> list[dict]:
        """转成待落库的消息行（每类一行；type 区分）。"""
        rows: list[dict] = []
        if self.thinking:
            rows.append({"role": "assistant", "type": "thinking", "content": self.thinking})
        for tl in self.tools:
            rows.append({"role": "assistant", "type": "tool",
                         "content": tl["result"], "extra": {
                             "name": tl["name"],
                             "source_favicons": tl.get("source_favicons") or [],
                         }})
        if self.plan:
            rows.append({"role": "assistant", "type": "plan", "extra": {"steps": self.plan}})
        for num in sorted(self.steps):
            st = self.steps[num]
            rows.append({"role": "assistant", "type": "step", "content": st["text"],
                         "extra": {"step_num": num, "task": st["task"],
                                   "thinking": st["thinking"], "tools": st["tools"]}})
        if self.text:
            rows.append({"role": "assistant", "type": "text", "content": self.text})
        return rows

    def memory_tool_results(self) -> list[str]:
        """Small textual tool outcomes suitable for the next-turn memory."""
        results = [
            f"{tool.get('name') or 'tool'}: {tool.get('result', '')}"
            for tool in self.tools
            if tool.get("result")
        ]
        for num in sorted(self.steps):
            for tool in self.steps[num]["tools"]:
                if tool.get("result"):
                    results.append(f"{tool.get('name') or 'tool'}: {tool.get('result', '')}")
        return results


@router.post("/{session_id}")
async def chat(session_id: str, body: ChatRequest):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    active = session.get("active_task")
    if session.get("request_active") or (active is not None and not active.done()):
        raise HTTPException(status_code=409, detail="A chat request is already active for this session")
    session["request_active"] = True
    session["last_access_at"] = time.monotonic()

    agent = session["agent"]
    mode = session["mode"]
    harness: HarnessConfig = session["harness"]
    memory: MemoryManager = session["memory"]
    tracker = BudgetTracker(harness)
    run_config = {
        # skill_call_counts：本轮 run_skill_script 的每 (skill,script) 计数容器，
        # 供 skills.py 的硬闸限制反复触发（如 web-research 搜索）。每轮新建即重置。
        "configurable": {
            "thread_id": session["thread_id"],
            "skill_call_counts": {},
            "max_skill_script_calls_per_task": harness.max_skill_script_calls_per_task,
        },
        "recursion_limit": harness.recursion_limit,
        "callbacks": [tracker],
    }
    short_msg = body.message[:60] + ("…" if len(body.message) > 60 else "")
    logger.info("chat request session=%s mode=%s message=%r", session_id, mode, short_msg)

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    # Let the HITL tool emit its question onto this request's SSE queue; the
    # request_user_choice tool then awaits the user's answer (POST /respond).
    hitl: HitlChannel = session["hitl"]
    hitl.bind_emit(queue.put_nowait)
    tool_start_callback = None
    if mode == "react":
        tool_start_callback = _SseToolStartCallback(queue.put_nowait)
        run_config["callbacks"].append(tool_start_callback)

    # 对话历史落库：session_id == chat_session.id。用户消息先落库、AI 产出累积后
    # 在流终止点落库（部分完成也存）。未配置 MySQL / 未登录则跳过。
    user_id = session.get("user_id")
    persist = db.enabled() and user_id is not None
    recorder = _TurnRecorder()

    async def persist_pending_memory_snapshot() -> None:
        if not persist:
            return
        snapshot = memory.pending_snapshot()
        if snapshot is None:
            return
        saved = await run_in_threadpool(
            db.save_chat_summary,
            session_id,
            user_id,
            snapshot.model_dump(mode="json"),
        )
        if saved:
            memory.mark_snapshot_persisted(snapshot.covered_through_seq)

    guardrail_decision = evaluate_input_guardrails(body.message, harness)
    if guardrail_decision.blocked:
        answer = guardrail_decision.response or ""
        guarded_item, _ = guard_output_item({"type": "text", "content": answer}, harness)

        async def guarded_stream():
            try:
                source_seqs: list[int] = []
                if persist:
                    await run_in_threadpool(
                        db.create_conversation, session_id, user_id, body.message[:24]
                    )
                    source_seqs.extend((await run_in_threadpool(
                        db.append_messages, session_id, user_id,
                        [{"role": "user", "type": "text", "content": body.message}],
                    )) or [])
                    source_seqs.extend((await run_in_threadpool(
                        db.append_messages, session_id, user_id,
                        [{"role": "assistant", "type": "text", "content": guarded_item.get("content", "")}],
                    )) or [])
                memory.commit_turn(
                    body.message,
                    guarded_item.get("content", ""),
                    source_seqs=source_seqs,
                )
                yield _sse(guarded_item)
                yield _sse({"type": "done"})
            finally:
                session["request_active"] = False
                session["last_access_at"] = time.monotonic()

        return StreamingResponse(
            guarded_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def produce_react():
        try:
            managed_messages = await memory.aprepare_messages(body.message, run_config)
            await persist_pending_memory_snapshot()
            async for chunk, metadata in agent.astream(
                {"messages": managed_messages},
                config=run_config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node")
                if node == "agent" and isinstance(chunk, AIMessageChunk):
                    for kind, text in iter_chunk_outputs(chunk):
                        if kind == "thinking":
                            await queue.put({"type": "thinking", "content": text})
                        else:
                            await queue.put({"type": "text", "content": text})
                elif node == "tools" and isinstance(chunk, ToolMessage):
                    result = (
                        chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                    )
                    name = str(chunk.name or "tool")
                    raw_tool_call_id = getattr(chunk, "tool_call_id", None)
                    tool_input = tool_start_callback.finish(raw_tool_call_id) if tool_start_callback else ""
                    await queue.put({
                        "type": "tool",
                        "name": name,
                        "result": result[:800],
                        "source_favicons": source_favicons_for_tool(name, result, tool_input),
                    })
        finally:
            await queue.put(SENTINEL)

    async def produce_plan_execute():
        try:
            conversation_context = await memory.aconversation_context(body.message, run_config)
            await persist_pending_memory_snapshot()
            async for mode, data in agent.astream(
                {
                    "input": body.message,
                    "conversation_context": conversation_context,
                    "plan": [],
                    "plan_total": 0,
                    "tasks": [],
                    "task_results": {},
                    "task_errors": {},
                    "past_steps": [],
                    "response": None,
                },
                config=run_config,
                stream_mode=["updates", "custom"],
            ):
                if mode == "custom":
                    phase = data.get("phase")
                    if phase == "planning_start":
                        await queue.put({"type": "phase", "phase": "planning"})
                    elif phase == "execute_start":
                        await queue.put({
                            "type": "step_start",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "total": data["total"],
                            "task": data["task"],
                        })
                    elif phase == "execute_token":
                        await queue.put({
                            "type": "step_token",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "text": data["text"],
                        })
                    elif phase == "execute_thinking":
                        await queue.put({
                            "type": "step_thinking",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "text": data["text"],
                        })
                    elif phase == "execute_tool":
                        await queue.put({
                            "type": "step_tool",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "tool_call_id": data.get("tool_call_id"),
                            "name": data["name"],
                            "result": data["result"],
                            "source_favicons": data.get("source_favicons") or [],
                        })
                    elif phase == "execute_tool_start":
                        await queue.put({
                            "type": "step_tool_start",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "tool_call_id": data.get("tool_call_id"),
                            "name": data["name"],
                            "input": data.get("input", ""),
                        })
                    elif phase == "execute_done":
                        await queue.put({
                            "type": "step_done",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                        })
                    elif phase == "execute_failed":
                        await queue.put({
                            "type": "step_failed",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "message": data["error"],
                        })
                    elif phase == "summarize_start":
                        await queue.put({"type": "phase", "phase": "summarizing"})
                    elif phase == "summarize_token":
                        await queue.put({"type": "text", "content": data["text"]})
                    elif phase == "summarize_thinking":
                        await queue.put({"type": "thinking", "content": data["text"]})
                else:  # mode == "updates"
                    for node, payload in data.items():
                        if node == "plan":
                            steps = payload.get("plan", [])
                            if steps:
                                await queue.put({"type": "plan", "steps": steps})
                        elif node == "execute":
                            for step, result in payload.get("past_steps", []):
                                await queue.put({"type": "step", "step": step, "result": result})
                        # summarize node intentionally not forwarded: its content
                        # was already streamed via summarize_token above.
        finally:
            await queue.put(SENTINEL)

    producer_coro = produce_react() if mode == "react" else produce_plan_execute()

    async def stream():
        t0 = time.monotonic()
        turn_complete = False
        user_source_seqs: list[int] = []
        # 用户消息先落库（首条消息时懒创建 chat_session，标题取首消息截断）。
        if persist:
            await run_in_threadpool(
                db.create_conversation, session_id, user_id, body.message[:24]
            )
            user_source_seqs = (await run_in_threadpool(
                db.append_messages, session_id, user_id,
                [{"role": "user", "type": "text", "content": body.message}],
            )) or []
        producer = asyncio.create_task(producer_coro)
        session["active_task"] = producer
        # idle timeout: each wait_for caps the gap between two events, not
        # the overall stream duration. If the producer is still running, a quiet
        # period means a long LLM/tool call is in flight, so emit a heartbeat
        # instead of killing the request.
        idle = harness.idle_timeout if harness.idle_timeout else None
        heartbeat_interval = min(15.0, idle) if idle is not None else None
        try:
            while True:
                # While a HITL question is outstanding, the human is deciding —
                # that is not an idle/stuck stream, so don't apply the heartbeat timeout.
                timeout = heartbeat_interval if heartbeat_interval is not None and not hitl.is_pending() else None
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    if producer.done():
                        continue
                    await queue.put({"type": "heartbeat"})
                    continue
                if item is SENTINEL:
                    break
                guarded_item, blocked_reason = guard_output_item(item, harness)
                if blocked_reason:
                    logger.info("stream sensitive output blocked session=%s reason=%s", session_id, blocked_reason)
                    yield _sse(guarded_item)
                    yield _sse({"type": "done"})
                    return
                recorder.observe(guarded_item)  # 累积 AI 产出用于落库
                yield _sse(guarded_item)

            # SENTINEL received — surface producer outcome
            try:
                await producer
            except asyncio.CancelledError:
                logger.info("stream cancelled session=%s", session_id)
                item, _ = guard_output_item(
                    {"type": "limit", "reason": "cancelled", "message": "cancelled by client"},
                    harness,
                )
                yield _sse(item)
                yield _sse({"type": "done"})
                return
            except BudgetExceededError as exc:
                logger.info("stream budget hit session=%s reason=%s",
                            session_id, exc.reason)
                item, _ = guard_output_item({"type": "limit", "reason": exc.reason, "message": exc.message}, harness)
                yield _sse(item)
                yield _sse({"type": "done"})
                return
            except GraphRecursionError as exc:
                logger.info("stream recursion limit session=%s", session_id)
                item, _ = guard_output_item({"type": "limit", "reason": "recursion", "message": str(exc)}, harness)
                yield _sse(item)
                yield _sse({"type": "done"})
                return
            except Exception as exc:
                if _is_content_moderation_error(exc):
                    logger.info("stream content moderation block session=%s", session_id)
                    item, _ = guard_output_item({
                        "type": "limit",
                        "reason": "content_filter",
                        "message": (
                            "内容被模型服务的安全审核拦截。这类限制由模型服务方"
                            "（通义千问/DashScope）在合规框架下施加，常见于政治/地缘、"
                            "金融投资建议（如股市预测）等敏感话题；请换个问法或更换话题。"
                        ),
                    }, harness)
                    yield _sse(item)
                    yield _sse({"type": "done"})
                    return
                logger.error("stream error session=%s: %s", session_id, exc, exc_info=exc)
                item, blocked_reason = guard_output_item({"type": "error", "message": str(exc)}, harness)
                yield _sse(item)
                if blocked_reason:
                    yield _sse({"type": "done"})
                return

            elapsed = time.monotonic() - t0
            logger.info("stream done session=%s elapsed=%.2fs", session_id, elapsed)
            turn_complete = True
            yield _sse({"type": "done"})
        finally:
            session["request_active"] = False
            session["active_task"] = None
            session["last_access_at"] = time.monotonic()
            if not producer.done():
                producer.cancel()
            # AI 产出落库（含被取消/中止/客户端断开时的部分产出）。
            assistant_source_seqs: list[int] = []
            if persist:
                rows = recorder.rows()
                if rows:
                    assigned_seqs = (await run_in_threadpool(
                        db.append_messages, session_id, user_id, rows
                    )) or []
                    assistant_source_seqs = [
                        seq for row, seq in zip(rows, assigned_seqs)
                        if row.get("type") == "text"
                    ]
                # 本轮 token 用量累加到 chat_session（每轮一次，不重复计）。
                await run_in_threadpool(
                    db.add_session_usage, session_id,
                    tracker.input_tokens, tracker.output_tokens, tracker.total_tokens,
                )
            memory.commit_turn(
                body.message,
                recorder.text,
                tool_results=recorder.memory_tool_results(),
                incomplete=not turn_complete,
                source_seqs=[*user_source_seqs, *assistant_source_seqs],
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # 禁止代理缓冲，保证 SSE 逐条实时下发（nginx 认 X-Accel-Buffering）。
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

