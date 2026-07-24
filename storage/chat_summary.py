"""Persistence helpers for the current rolling memory summary per chat."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from logger import get_logger
from storage.chat_message import ChatMessage
from storage.chat_session import ChatSession
from storage.db import Base, _now_ms, enabled, session_scope

logger = get_logger("db")


class ChatSummary(Base):
    """Latest rolling summary for a conversation.

    ``session_id`` intentionally has a normal, non-unique index. The current
    single-row invariant is enforced transactionally so future topic summaries
    can reuse this table without a primary-key migration.
    """

    __tablename__ = "chat_summary"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    covered_from_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    covered_through_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    covered_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_time: Mapped[int] = mapped_column(BigInteger, nullable=False)


def _text_messages(s, session_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    rows = s.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .where(ChatMessage.type == "text")
        .where(ChatMessage.seq > after_seq)
        .order_by(ChatMessage.seq)
    ).all()
    return [
        {
            "seq": row.seq,
            "role": row.role,
            "type": row.type,
            "content": row.content,
            "extra": row.extra,
            "create_time": row.create_time,
        }
        for row in rows
    ]


def load_memory_state(
    session_id: str,
    user_id: str,
    *,
    supported_summary_version: int = 1,
) -> dict[str, Any] | None:
    """Return a validated summary plus only its uncovered text-message tail.

    Missing/invalid summaries gracefully fall back to all text messages. A
    missing conversation or owner mismatch returns ``None``.
    """
    if not enabled():
        return None
    try:
        with session_scope() as s:
            conv = s.get(ChatSession, session_id)
            if conv is None or conv.user_id != user_id:
                return None
            rows = s.scalars(
                select(ChatSummary)
                .where(ChatSummary.session_id == session_id)
                .order_by(
                    ChatSummary.covered_through_seq.desc(),
                    ChatSummary.update_time.desc(),
                    ChatSummary.id.desc(),
                )
            ).all()
            summary_row = rows[0] if rows else None
            max_seq = int(s.scalar(
                select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
            ) or 0)
            valid = (
                summary_row is not None
                and summary_row.summary_version == supported_summary_version
                and isinstance(summary_row.summary, dict)
                and summary_row.covered_from_seq > 0
                and summary_row.covered_from_seq <= summary_row.covered_through_seq
                and summary_row.covered_through_seq <= max_seq
                and summary_row.covered_message_count > 0
            )
            if not valid:
                return {"snapshot": None, "messages": _text_messages(s, session_id)}
            snapshot = {
                "summary": summary_row.summary,
                "covered_from_seq": summary_row.covered_from_seq,
                "covered_through_seq": summary_row.covered_through_seq,
                "covered_message_count": summary_row.covered_message_count,
                "summary_version": summary_row.summary_version,
                "estimated_tokens": summary_row.estimated_tokens,
            }
            return {
                "snapshot": snapshot,
                "messages": _text_messages(s, session_id, summary_row.covered_through_seq),
            }
    except Exception:
        logger.exception("load_memory_state 失败 session=%s", session_id)
        return None


def save_chat_summary(session_id: str, user_id: str, snapshot: dict[str, Any]) -> bool:
    """Insert or monotonically update the one current summary for a chat.

    Locking the parent chat_session row serializes writers without placing a
    unique constraint on chat_summary.session_id. Unexpected duplicates are
    reconciled in the same transaction.
    """
    if not enabled():
        return False
    try:
        incoming_from = int(snapshot["covered_from_seq"])
        incoming_through = int(snapshot["covered_through_seq"])
        incoming_count = int(snapshot["covered_message_count"])
        summary_payload = snapshot["summary"]
        if (
            not isinstance(summary_payload, dict)
            or incoming_from <= 0
            or incoming_from > incoming_through
            or incoming_count <= 0
        ):
            return False
        now = _now_ms()
        with session_scope(write=True) as s:
            conv = s.scalar(
                select(ChatSession)
                .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
                .with_for_update()
            )
            if conv is None:
                return False
            max_seq = int(s.scalar(
                select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
            ) or 0)
            if incoming_through > max_seq:
                return False
            rows = s.scalars(
                select(ChatSummary)
                .where(ChatSummary.session_id == session_id)
                .order_by(
                    ChatSummary.covered_through_seq.desc(),
                    ChatSummary.update_time.desc(),
                    ChatSummary.id.desc(),
                )
            ).all()
            current = rows[0] if rows else None
            for duplicate in rows[1:]:
                s.delete(duplicate)

            if current is not None and incoming_through <= current.covered_through_seq:
                return True
            if current is not None and incoming_count < current.covered_message_count:
                return False
            values = {
                "user_id": user_id,
                "summary": summary_payload,
                "covered_from_seq": (
                    min(current.covered_from_seq, incoming_from) if current else incoming_from
                ),
                "covered_through_seq": incoming_through,
                "covered_message_count": incoming_count,
                "summary_version": int(snapshot.get("summary_version", 1)),
                "estimated_tokens": int(snapshot.get("estimated_tokens", 0)),
            }
            if current is None:
                s.add(ChatSummary(
                    session_id=session_id,
                    create_time=now,
                    update_time=now,
                    **values,
                ))
            else:
                for key, value in values.items():
                    setattr(current, key, value)
                current.update_time = now
            return True
    except Exception:
        logger.exception("save_chat_summary 失败 session=%s", session_id)
        return False
