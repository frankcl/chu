"""Conversation history, title, and ownership endpoints."""

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent import LLM
from logger import get_logger
import storage as db

from .auth import current_user_id
from .runtime import sessions, teardown_session

logger = get_logger("web_api.conversations")
router = APIRouter(prefix="/api", tags=["conversations"])

_TITLE_SYSTEM = (
    "你是会话标题生成器。根据用户的消息，用简洁的中文概括其主题，生成一个不超过 12 个字"
    "的短标题。只输出标题本身，不要引号、标点符号、序号或任何多余解释。"
)


class TitleRequest(BaseModel):
    message: str


class TitleUpdate(BaseModel):
    title: str


class TopUpdate(BaseModel):
    top: bool


@router.post("/title")
def generate_title(body: TitleRequest):
    text = body.message.strip()
    fallback = text[:24]
    if not text:
        return {"title": fallback}
    try:
        response = LLM().chat_model(thinking=False).invoke([
            SystemMessage(content=_TITLE_SYSTEM),
            HumanMessage(content=text[:500]),
        ])
        title = LLM.extract_text(response.content).strip().strip("\"'《》 ")
        title = title.splitlines()[0][:20] if title else ""
        return {"title": title or fallback}
    except Exception as exc:
        logger.warning("title generation failed: %s", exc)
        return {"title": fallback}


@router.get("/conversations")
def list_conversations(start_time: int | None = None, end_time: int | None = None):
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "conversations": db.list_conversations(
            user_id, start_time=start_time, end_time=end_time,
        )
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    messages = db.get_messages(conversation_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conversation_id, "messages": messages}


@router.delete("/conversations")
def clear_conversations():
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    for session_id, record in list(sessions.items()):
        if record.get("user_id") == user_id:
            teardown_session(session_id)
    deleted = db.delete_user_history(user_id)
    logger.info("conversations cleared user=%s db_deleted=%d", user_id, len(deleted))
    return {"ok": True, "deleted": len(deleted)}


@router.post("/conversations/{conversation_id}/title")
def set_conversation_title(conversation_id: str, body: TitleUpdate):
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    if db.get_owner(conversation_id) != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.update_title(conversation_id, body.title)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/top")
def set_conversation_top(conversation_id: str, body: TopUpdate):
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    if not db.set_top(conversation_id, user_id, body.top):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    record = sessions.get(conversation_id)
    owns_runtime = record is not None and record.get("user_id") == user_id
    owns_db = db.get_owner(conversation_id) == user_id
    if not owns_runtime and not owns_db:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if owns_runtime:
        teardown_session(conversation_id)
    if owns_db:
        db.delete_conversation(conversation_id, user_id)
    logger.info(
        "conversation deleted id=%s runtime=%s db=%s",
        conversation_id, owns_runtime, owns_db,
    )
    return {"ok": True}
