"""Database bootstrap and session management for persistent storage."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from logger import get_logger
from utils.env_util import env_bool, env_int, env_str

logger = get_logger("db")

DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT = 30
DEFAULT_POOL_RECYCLE = 3600
DEFAULT_POOL_PRE_PING = True
DEFAULT_ISOLATION_LEVEL = "READ COMMITTED"


def _now_ms() -> int:
    return int(time.time() * 1000)


class Base(DeclarativeBase):
    pass


def _build_url() -> str | None:
    """从环境变量拼数据库 URL；优先整串 DATABASE_URL，否则用 MYSQL_* 拼。

    缺少关键项（host/user/database）则返回 None —— 视为「未配置」，历史功能禁用。
    """
    url = env_str("DATABASE_URL")
    if url:
        return url
    host = env_str("MYSQL_HOST")
    user = env_str("MYSQL_USER")
    database = env_str("MYSQL_DATABASE")
    if not (host and user and database):
        return None
    port = env_str("MYSQL_PORT", "3306")
    password = env_str("MYSQL_PASSWORD")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def _engine_options() -> dict[str, object]:
    return {
        "pool_size": env_int("DB_POOL_SIZE", DEFAULT_POOL_SIZE, logger),
        "max_overflow": env_int("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW, logger),
        "pool_timeout": env_int("DB_POOL_TIMEOUT", DEFAULT_POOL_TIMEOUT, logger),
        "pool_recycle": env_int("DB_POOL_RECYCLE", DEFAULT_POOL_RECYCLE, logger),
        "pool_pre_ping": env_bool("DB_POOL_PRE_PING", DEFAULT_POOL_PRE_PING, logger),
        "isolation_level": DEFAULT_ISOLATION_LEVEL,
        "future": True,
    }


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
        # Import models before create_all so their tables are registered on Base.
        from storage import chat_message, chat_session, chat_summary  # noqa: F401

        _engine = create_engine(url, **_engine_options())
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine, future=True)
        logger.info("对话历史 MySQL 已就绪")
        return True
    except Exception:
        logger.exception("MySQL 初始化失败，对话历史功能禁用")
        _engine = None
        _Session = None
        return False


def enabled() -> bool:
    return _Session is not None


@contextmanager
def session_scope(*, write: bool = False) -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("database storage is disabled")
    if write:
        with _Session() as session, session.begin():
            yield session
    else:
        with _Session() as session:
            yield session
