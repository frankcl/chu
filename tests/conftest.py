"""Shared fixtures and environment setup for all tests."""

import gc
import os
import pytest

# ---------------------------------------------------------------------------
# Set dummy env vars BEFORE any agent module is imported, so that
# provider-specific constructors (ChatAnthropic, ChatOpenAI …) receive
# non-empty values and don't complain about missing API keys.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-dashscope-key")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key")

# Hylian SSO 配置：在 import server 之前置好，使 enable_hylian_shield() 的
# config.check() 通过。load_dotenv() 不会覆盖已存在的环境变量，故测试值优先于 .env。
os.environ.setdefault("HYLIAN_APP_ID", "test-app")
os.environ.setdefault("HYLIAN_APP_SECRET", "test-secret")
os.environ.setdefault("HYLIAN_SERVER_URL", "https://hylian.test/")
# 测试走 http，关闭 secure，避免 sid cookie 因 secure 而不写入（影响 cookie 会话用例）。
os.environ.setdefault("HYLIAN_SESSION_COOKIE_SECURE", "false")

# Disable langsmith / langchain tracing so the pytest plugin doesn't buffer
# per-run trace events in memory (the plugin loads automatically with the
# langsmith package and accumulates even when no API key is configured).
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING_V2"] = "false"

# 测试环境强制禁用真实数据库：清空 MySQL 配置，使 storage.db._build_url() 返回 None，
# init_db() 优雅降级（不连库、不建表/ALTER、所有读写 no-op）。防止 import server
# 时连上 .env 里的真实 MySQL 造成污染。用赋值（非 setdefault）确保盖过 .env，
# 且 load_dotenv() 默认 override=False 不会回写。需要落库的用例用 db_rollback
# fixture（内存 SQLite + 回滚）自行绑定隔离库。
os.environ["DATABASE_URL"] = ""
os.environ["MYSQL_HOST"] = ""
os.environ["MYSQL_USER"] = ""
os.environ["MYSQL_DATABASE"] = ""


# ---------------------------------------------------------------------------
# web_api.runtime.sessions is a module-level dict. Each test that creates a session
# adds an entry (with a compiled mock agent + MemoryManager + active_task slot).
# Without per-test cleanup it grows monotonically across
# the whole suite — visible as "memory keeps climbing" when running pytest.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _no_real_db():
    """回归护栏：整轮测试开始时断言真实数据库未被绑定。

    正常情况下上面清空的 MySQL 配置会让 init_db() 优雅降级（enabled() 为 False）。
    若将来有人改坏隔离（例如重新配置了真实库），这里 fail-fast，避免污染生产库。
    需要落库的用例请用 db_rollback fixture。
    """
    import storage as db
    assert not db.enabled(), (
        "测试不得连接真实数据库：init_db 应因空 MySQL 配置而禁用。"
        "如需落库请用 db_rollback fixture。"
    )
    yield


@pytest.fixture(autouse=True)
def _clear_server_sessions_after_test():
    yield
    try:
        from web_api.runtime import sessions
        if sessions:
            # cancel any leftover active tasks before dropping refs
            for s in list(sessions.values()):
                task = s.get("active_task") if isinstance(s, dict) else None
                if task is not None and not task.done():
                    task.cancel()
            sessions.clear()
    except Exception:
        pass
    # Force a GC pass — MagicMock trees, langchain RunnableConfig contexts and
    # asyncio task wrappers form cycles that the cycle collector reclaims
    # promptly only when nudged; otherwise RSS keeps climbing across tests
    # until generation-2 collection naturally fires.
    gc.collect()


# ---------------------------------------------------------------------------
# DB tests: transaction-rollback isolation.
#
# Every storage helper commits internally (`with _Session() as s, s.begin()`).
# To keep DB testcases from persisting anything, we bind the db module to an
# in-memory SQLite connection wrapped in a single outer transaction, and use
# join_transaction_mode="create_savepoint" so each helper's commit only
# releases a SAVEPOINT. At teardown one rollback() discards the whole test's
# writes — nothing is ever committed. Use via the `db_rollback` arg; it yields
# the patched storage module.
# ---------------------------------------------------------------------------
@pytest.fixture
def db_rollback():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import storage as storage_api
    import storage.db as db

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared in-memory DB across connections
    )

    # pysqlite 会自作主张地开启/提交事务，破坏 SAVEPOINT 嵌套，导致外层 rollback 无效。
    # 按 SQLAlchemy 官方文档关掉它的隐式 BEGIN，改由 SQLAlchemy 显式发 BEGIN，
    # 这样各 helper 内部 commit 只释放 SAVEPOINT，外层 rollback 才能真正丢弃全部写入。
    @event.listens_for(engine, "connect")
    def _sqlite_disable_autobegin(dbapi_conn, _record):
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_emit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    db.Base.metadata.create_all(engine)
    connection = engine.connect()
    outer = connection.begin()
    TestSession = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")

    saved_engine, saved_session = db._engine, db._Session
    db._engine, db._Session = engine, TestSession
    try:
        yield storage_api
    finally:
        db._engine, db._Session = saved_engine, saved_session
        if outer.is_active:
            outer.rollback()  # 丢弃本测试的一切写入
        connection.close()
        engine.dispose()
