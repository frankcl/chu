"""Business helpers for chat messages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, Text, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from logger import get_logger
from storage.chat_session import ChatSession
from storage.db import Base, _now_ms, enabled, session_scope

logger = get_logger("db")


class ChatMessage(Base):
    """消息：区分 role（user/assistant）与 type（text/thinking/tool/plan/step）。"""

    __tablename__ = "chat_message"

    # BigInteger 在 MySQL 是 BIGINT AUTO_INCREMENT；SQLite 只对 INTEGER 主键自增，
    # 故用 variant 降级为 INTEGER，保证测试/本地用 SQLite 也能自增。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    # 冗余记录归属用户，便于按用户删除/统计消息（无需 join chat_session）。
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="", server_default=text("''"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    create_time: Mapped[int] = mapped_column(BigInteger)
    update_time: Mapped[int] = mapped_column(BigInteger)


def append_messages(session_id: str, user_id: str, rows: list[dict[str, Any]]) -> list[int]:
    """追加若干消息，seq 从当前最大值 +1 起自增，并刷新 session.update_time。

    每个 row: {role, type, content?, extra?}。返回与 rows 对齐的 seq 列表；
    未启用存储、空列表或写入失败时返回空列表。user_id 冗余写入每行。
    """
    if not enabled() or not rows:
        return []
    now = _now_ms()
    assigned: list[int] = []
    try:
        with session_scope(write=True) as s:
            max_seq = s.scalar(
                select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
            )
            seq = (max_seq or 0) + 1
            for row in rows:
                assigned.append(seq)
                s.add(ChatMessage(
                    session_id=session_id,
                    user_id=user_id or "",
                    seq=seq,
                    role=row["role"],
                    type=row["type"],
                    content=row.get("content", "") or "",
                    extra=row.get("extra"),
                    create_time=now,
                    update_time=now,
                ))
                seq += 1
            conv = s.get(ChatSession, session_id)
            if conv is not None:
                conv.update_time = now
        return assigned
    except Exception:
        logger.exception("append_messages 失败 session=%s", session_id)
        return []


def get_messages(session_id: str, user_id: str) -> list[dict[str, Any]] | None:
    """该 session 的全部消息（按 seq 升序）。非 owner 或不存在返回 None。"""
    if not enabled():
        return None
    try:
        with session_scope() as s:
            conv = s.get(ChatSession, session_id)
            if conv is None or conv.user_id != user_id:
                return None
            rows = s.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.seq)
            ).all()
            return [
                {
                    "seq": r.seq,
                    "role": r.role,
                    "type": r.type,
                    "content": r.content,
                    "extra": r.extra,
                    "create_time": r.create_time,
                }
                for r in rows
            ]
    except Exception:
        logger.exception("get_messages 失败 session=%s", session_id)
        return None
