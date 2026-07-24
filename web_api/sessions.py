"""Runtime chat session endpoints."""

import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from harness import HarnessConfig
from logger import get_logger
import storage as db

from .auth import current_user_id
from .runtime import build_session_record, rebuild_memory, sessions, teardown_session

logger = get_logger("web_api.sessions")
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionRequest(BaseModel):
    mode: str = "react"
    conversation_id: str | None = None
    recursion_limit: int | None = None
    idle_timeout: float | None = None
    per_tool_timeout: float | None = None
    max_tool_calls: int | None = None
    max_tool_calls_per_task: int | None = None
    max_skill_script_calls_per_task: int | None = None
    max_parallel_tasks: int | None = None
    max_tokens: int | None = None
    llm_max_retries: int | None = None
    tool_allowlist: list[str] | None = None
    tool_denylist: list[str] | None = None
    enabled_guardrails: list[str] | None = None
    sensitive_output_scan: bool | None = None
    sensitive_output_action: str | None = None
    memory_max_tokens: int | None = None
    memory_target_tokens: int | None = None
    memory_keep_recent_turns: int | None = None
    memory_ttl_seconds: float | None = None


def _harness_from_request(body: SessionRequest) -> HarnessConfig:
    overrides = body.model_dump(exclude={"mode"}, exclude_none=True)
    return HarnessConfig.from_env().merge(overrides)


@router.post("")
def create_session(body: SessionRequest):
    user_id = current_user_id()
    try:
        harness = _harness_from_request(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.conversation_id:
        session_id = body.conversation_id
        if db.get_owner(session_id) != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        existing = sessions.get(session_id)
        if existing is None or existing.get("mode") != body.mode:
            record = build_session_record(session_id, body.mode, harness, user_id)
            count = rebuild_memory(record, session_id, user_id)
            sessions[session_id] = record
            logger.info(
                "session continued id=%s mode=%s rebuilt_msgs=%d replaced=%s",
                session_id, body.mode, count, existing is not None,
            )
        else:
            existing["last_access_at"] = time.monotonic()
    else:
        session_id = str(uuid.uuid4())
        sessions[session_id] = build_session_record(session_id, body.mode, harness, user_id)
        logger.info("session created id=%s mode=%s harness=%s", session_id, body.mode, harness)
    return {"session_id": session_id, "mode": body.mode}


@router.delete("/{session_id}")
def delete_session(session_id: str):
    teardown_session(session_id)
    logger.info("session deleted id=%s", session_id)
    return {"ok": True}
