"""对话历史持久化（MySQL）。存储层，与 agent 运行时解耦。

两个维度：
- ``chat_session``（对话 session）：一场对话，id 即 LangGraph ``thread_id`` /
  运行时 session_id —— 历史与「对话记忆」通过这个同一 id 关联。
- ``chat_message``（消息，区分类型）：``role`` × ``type``（text/thinking/tool/
  plan/step）全量记录；同一 session 下按 ``seq`` 排序组成完整对话。

这里的「对话历史」是全量、持久、可浏览的记录，区别于「对话记忆」（LangGraph
checkpointer 里喂给 LLM 的上下文）。未配置 MySQL 或连接失败时，所有 helper 优雅
降级（写入静默、读取返回空），不阻断对话主流程 —— 与 OSS 未配置时同样的思路。

所有表统一 ``create_time`` / ``update_time``，均为毫秒时间戳（BIGINT），与 hylian
models 的时间字段约定对齐。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .log import get_logger

logger = get_logger("db")


def _now_ms() -> int:
    return int(time.time() * 1000)


class Base(DeclarativeBase):
    pass


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


def _build_url() -> str | None:
    """从环境变量拼数据库 URL；优先整串 DATABASE_URL，否则用 MYSQL_* 拼。

    缺少关键项（host/user/database）则返回 None —— 视为「未配置」，历史功能禁用。
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("MYSQL_HOST")
    user = os.getenv("MYSQL_USER")
    database = os.getenv("MYSQL_DATABASE")
    if not (host and user and database):
        return None
    port = os.getenv("MYSQL_PORT", "3306")
    password = os.getenv("MYSQL_PASSWORD", "")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


_engine = None
_Session: sessionmaker | None = None


def init_db() -> bool:
    """构建 Engine 并建表。成功返回 True；未配置或失败返回 False（历史功能禁用）。"""
    global _engine, _Session
    url = _build_url()
    if url is None:
        logger.warning("MySQL 未配置（缺 DATABASE_URL 或 MYSQL_*），对话历史功能禁用")
        return False
    try:
        _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600, future=True)
        Base.metadata.create_all(_engine)
        _ensure_schema(_engine)
        _Session = sessionmaker(bind=_engine, future=True)
        logger.info("对话历史 MySQL 已就绪")
        return True
    except Exception:
        logger.exception("MySQL 初始化失败，对话历史功能禁用")
        _engine = None
        _Session = None
        return False


def _ensure_schema(engine) -> None:
    """为既有表补齐后加的列（create_all 不会 ALTER 已存在的表）。best-effort。"""
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("chat_session")}
        if "top" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE chat_session ADD COLUMN top TINYINT(1) NOT NULL DEFAULT 0"
                ))
            logger.info("chat_session 增加 top 列")
    except Exception:
        logger.exception("chat_session schema 迁移失败（top 列）")


def enabled() -> bool:
    return _Session is not None


# ---- helper：写入 ----

def create_conversation(session_id: str, user_id: str, title: str = "") -> None:
    """懒创建一场对话（首条消息落库时调用）。id 由调用方给定（= thread_id）。"""
    if _Session is None:
        return
    now = _now_ms()
    try:
        with _Session() as s, s.begin():
            if s.get(ChatSession, session_id) is not None:
                return
            s.add(ChatSession(
                id=session_id, user_id=user_id, title=title[:64],
                create_time=now, update_time=now,
            ))
    except Exception:
        logger.exception("create_conversation 失败 session=%s", session_id)


def append_messages(session_id: str, user_id: str, rows: list[dict[str, Any]]) -> None:
    """追加若干消息，seq 从当前最大值 +1 起自增，并刷新 session.update_time。

    每个 row: {role, type, content?, extra?}。空列表直接返回。user_id 冗余写入每行。
    """
    if _Session is None or not rows:
        return
    now = _now_ms()
    try:
        with _Session() as s, s.begin():
            max_seq = s.scalar(
                select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
            )
            seq = (max_seq or 0) + 1
            for row in rows:
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
    except Exception:
        logger.exception("append_messages 失败 session=%s", session_id)


def update_title(session_id: str, title: str) -> None:
    if _Session is None or not title:
        return
    try:
        with _Session() as s, s.begin():
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
    if _Session is None or not (input_tokens or output_tokens or total_tokens):
        return
    try:
        with _Session() as s, s.begin():
            conv = s.get(ChatSession, session_id)
            if conv is not None:
                conv.input_tokens += int(input_tokens or 0)
                conv.output_tokens += int(output_tokens or 0)
                conv.total_tokens += int(total_tokens or 0)
                conv.update_time = _now_ms()
    except Exception:
        logger.exception("add_session_usage 失败 session=%s", session_id)


# ---- helper：读取 / 删除 ----

def list_conversations(user_id: str) -> list[dict[str, Any]]:
    """当前用户的对话列表，按 update_time 倒序。"""
    if _Session is None:
        return []
    try:
        with _Session() as s:
            rows = s.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.top.desc(), ChatSession.update_time.desc())
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
    if _Session is None:
        return {"total": dict(empty), "daily": []}
    try:
        with _Session() as s:
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
    if _Session is None:
        return None
    try:
        with _Session() as s:
            conv = s.get(ChatSession, session_id)
            return conv.user_id if conv is not None else None
    except Exception:
        logger.exception("get_owner 失败 session=%s", session_id)
        return None


def get_messages(session_id: str, user_id: str) -> list[dict[str, Any]] | None:
    """该 session 的全部消息（按 seq 升序）。非 owner 或不存在返回 None。"""
    if _Session is None:
        return None
    try:
        with _Session() as s:
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


def set_top(session_id: str, user_id: str, top: bool) -> bool:
    """置顶/取消置顶。非 owner 或不存在返回 False。不改 update_time（不影响排序基准）。"""
    if _Session is None:
        return False
    try:
        with _Session() as s, s.begin():
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
    if _Session is None:
        return False
    try:
        with _Session() as s, s.begin():
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
    if _Session is None:
        return []
    try:
        with _Session() as s, s.begin():
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
