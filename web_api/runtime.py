"""Shared runtime state and lifecycle for web chat sessions."""

import asyncio
import time

from agent import HitlChannel, create_agent, create_plan_execute_agent
from harness import HarnessConfig
from logger import get_logger
from memory import MemoryManager, MemorySnapshot
import storage as db
from utils.env_util import env_float

logger = get_logger("web_api.runtime")

# Persistent history lives in storage; this cache contains runtime-only objects.
sessions: dict[str, dict] = {}


def build_session_record(
    session_id: str,
    mode: str,
    harness: HarnessConfig,
    user_id: str | None,
) -> dict:
    hitl = HitlChannel()
    if mode == "plan-execute":
        agent = create_plan_execute_agent(harness=harness, hitl_channel=hitl)
    else:
        agent = create_agent(harness=harness, hitl_channel=hitl)
    return {
        "agent": agent,
        "mode": mode,
        "thread_id": session_id,
        "harness": harness,
        "memory": MemoryManager(harness=harness),
        "active_task": None,
        "request_active": False,
        "hitl": hitl,
        "user_id": user_id,
        "last_access_at": time.monotonic(),
    }


def rebuild_memory(record: dict, session_id: str, user_id: str) -> int:
    state = db.load_memory_state(session_id, user_id)
    if state is None:
        history = db.get_messages(session_id, user_id) or []
        record["memory"].load_history(history)
        return sum(1 for row in history if row.get("type") == "text" and row.get("content"))
    snapshot = None
    if state.get("snapshot") is not None:
        try:
            snapshot = MemorySnapshot.model_validate(state["snapshot"])
        except Exception as exc:
            logger.warning("invalid memory snapshot session=%s: %s", session_id, exc)
            history = db.get_messages(session_id, user_id) or []
            record["memory"].load_history(history)
            return sum(1 for row in history if row.get("type") == "text" and row.get("content"))
    messages = state.get("messages") or []
    record["memory"].restore(snapshot, messages)
    return len(messages)


def teardown_session(session_id: str) -> bool:
    session = sessions.pop(session_id, None)
    if not session:
        return False
    if session.get("hitl"):
        session["hitl"].cancel()
    if session.get("active_task"):
        session["active_task"].cancel()
    memory = session.get("memory")
    if memory is not None:
        memory.clear()
    return True


def evict_idle_sessions(now: float | None = None) -> list[str]:
    now = time.monotonic() if now is None else now
    evicted: list[str] = []
    for session_id, record in list(sessions.items()):
        task = record.get("active_task")
        hitl = record.get("hitl")
        if (
            record.get("request_active")
            or (task is not None and not task.done())
            or (hitl is not None and hitl.is_pending())
        ):
            continue
        ttl = record["harness"].memory_ttl_seconds
        if now - record.get("last_access_at", now) >= ttl and teardown_session(session_id):
            evicted.append(session_id)
            logger.info("idle runtime session evicted id=%s ttl=%.1fs", session_id, ttl)
    return evicted


async def memory_sweeper() -> None:
    interval = max(1.0, env_float("MEMORY_SWEEP_INTERVAL_SECONDS", 60.0, logger))
    while True:
        await asyncio.sleep(interval)
        evict_idle_sessions()
