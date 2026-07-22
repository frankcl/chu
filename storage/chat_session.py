"""Business helpers for chat sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, String, and_, false, or_, select, text
from sqlalchemy.orm import Mapped, mapped_column

from logger import get_logger
from storage.db import Base, _now_ms, enabled, session_scope

logger = get_logger("db")


class ChatSession(Base):
    """对话 session：一场对话。id = thread_id = 运行时 session_id。"""

    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(64), default="")
    # 累计 token 用量（本场对话所有轮次之和）。逐消息归属不准，只在 session 级统计。
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    # 是否置顶：置顶对话在历史列表中优先展示。
    top: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    create_time: Mapped[int] = mapped_column(BigInteger)
    update_time: Mapped[int] = mapped_column(BigInteger)


def create_conversation(session_id: str, user_id: str, title: str = "") -> None:
    """懒创建一场对话（首条消息落库时调用）。id 由调用方给定（= thread_id）。"""
    if not enabled():
        return
    now = _now_ms()
    try:
        with session_scope(write=True) as s:
            if s.get(ChatSession, session_id) is not None:
                return
            s.add(ChatSession(
                id=session_id, user_id=user_id, title=title[:64],
                create_time=now, update_time=now,
            ))
    except Exception:
        logger.exception("create_conversation 失败 session=%s", session_id)


def update_title(session_id: str, title: str) -> None:
    if not enabled() or not title:
        return
    try:
        with session_scope(write=True) as s:
            conv = s.get(ChatSession, session_id)
            if conv is not None:
                conv.title = title[:64]
                conv.update_time = _now_ms()
    except Exception:
        logger.exception("update_title 失败 session=%s", session_id)


def add_session_usage(
    session_id: str, input_tokens: int, output_tokens: int, total_tokens: int
) -> None:
    """把一轮对话的 token 用量累加到 chat_session；session 不存在则 no-op。"""
    if not enabled() or not (input_tokens or output_tokens or total_tokens):
        return
    try:
        with session_scope(write=True) as s:
            conv = s.get(ChatSession, session_id)
            if conv is not None:
                conv.input_tokens += int(input_tokens or 0)
                conv.output_tokens += int(output_tokens or 0)
                conv.total_tokens += int(total_tokens or 0)
                conv.update_time = _now_ms()
    except Exception:
        logger.exception("add_session_usage 失败 session=%s", session_id)


def list_conversations(
    user_id: str,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[dict[str, Any]]:
    """当前用户的对话列表，按 update_time 倒序，可按毫秒时间戳过滤。"""
    if not enabled():
        return []
    end_time = _now_ms() if end_time is None else end_time
    try:
        with session_scope() as s:
            time_filters = [ChatSession.update_time <= end_time]
            if start_time is not None:
                time_filters.append(ChatSession.update_time >= start_time)
            in_time_range = false() if start_time is not None and start_time > end_time else and_(*time_filters)
            stmt = (
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .where(or_(ChatSession.top.is_(True), in_time_range))
            )
            rows = s.scalars(
                stmt.order_by(ChatSession.top.desc(), ChatSession.update_time.desc())
            ).all()
            return [
                {"id": r.id, "title": r.title, "update_time": r.update_time, "top": bool(r.top)}
                for r in rows
            ]
    except Exception:
        logger.exception("list_conversations 失败 user=%s", user_id)
        return []


def user_token_stats(user_id: str) -> dict[str, Any]:
    """当前用户的 token 消耗统计：总量 + 按日期趋势。

    趋势用每场对话的 create_time 所在（本地）日期近似（跨天会话计入创建日）。
    在 Python 里分桶，避免 DB 方言差异（毫秒时间戳 → 日期）。
    """
    empty = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if not enabled():
        return {"total": dict(empty), "daily": []}
    try:
        with session_scope() as s:
            rows = s.execute(
                select(
                    ChatSession.create_time,
                    ChatSession.input_tokens,
                    ChatSession.output_tokens,
                    ChatSession.total_tokens,
                ).where(ChatSession.user_id == user_id)
            ).all()
    except Exception:
        logger.exception("user_token_stats 失败 user=%s", user_id)
        return {"total": dict(empty), "daily": []}

    total = dict(empty)
    buckets: dict[str, dict[str, int]] = {}
    for create_time, inp, outp, tot in rows:
        inp, outp, tot = int(inp or 0), int(outp or 0), int(tot or 0)
        total["input_tokens"] += inp
        total["output_tokens"] += outp
        total["total_tokens"] += tot
        day = datetime.fromtimestamp((create_time or 0) / 1000).strftime("%Y-%m-%d")
        b = buckets.setdefault(day, dict(empty))
        b["input_tokens"] += inp
        b["output_tokens"] += outp
        b["total_tokens"] += tot
    daily = [{"date": d, **buckets[d]} for d in sorted(buckets)]
    return {"total": total, "daily": daily}


def get_owner(session_id: str) -> str | None:
    """返回该 session 的 owner user_id；不存在返回 None。"""
    if not enabled():
        return None
    try:
        with session_scope() as s:
            conv = s.get(ChatSession, session_id)
            return conv.user_id if conv is not None else None
    except Exception:
        logger.exception("get_owner 失败 session=%s", session_id)
        return None


def set_top(session_id: str, user_id: str, top: bool) -> bool:
    """置顶/取消置顶。非 owner 或不存在返回 False。不改 update_time（不影响排序基准）。"""
    if not enabled():
        return False
    try:
        with session_scope(write=True) as s:
            conv = s.get(ChatSession, session_id)
            if conv is None or conv.user_id != user_id:
                return False
            conv.top = bool(top)
            return True
    except Exception:
        logger.exception("set_top 失败 session=%s", session_id)
        return False


def delete_conversation(session_id: str, user_id: str) -> bool:
    """删除对话及其消息。非 owner 或不存在返回 False。"""
    if not enabled():
        return False
    try:
        from storage.chat_message import ChatMessage

        with session_scope(write=True) as s:
            conv = s.get(ChatSession, session_id)
            if conv is None or conv.user_id != user_id:
                return False
            s.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
            s.delete(conv)
            return True
    except Exception:
        logger.exception("delete_conversation 失败 session=%s", session_id)
        return False


def delete_user_history(user_id: str) -> list[str]:
    """删除指定用户的所有对话与消息。返回被删的 session id 列表（供调用方清运行时）。"""
    if not enabled():
        return []
    try:
        from storage.chat_message import ChatMessage

        with session_scope(write=True) as s:
            session_ids = list(s.scalars(
                select(ChatSession.id).where(ChatSession.user_id == user_id)
            ).all())
            # 按 user_id 直接删消息（无需 join）；再删会话行。
            s.query(ChatMessage).filter(ChatMessage.user_id == user_id).delete()
            s.query(ChatSession).filter(ChatSession.user_id == user_id).delete()
            return session_ids
    except Exception:
        logger.exception("delete_user_history 失败 user=%s", user_id)
        return []
