"""对话历史持久化层（storage）的测试。

全部用 `db_rollback` fixture（见 conftest.py）：每个用例在一个外层事务里跑，
内部各 helper 的 commit 只落到 SAVEPOINT，测试结束统一 rollback —— 不留任何数据。
"""


def test_init_db_uses_default_engine_options(monkeypatch):
    import storage.db as storage_db

    captured = {}
    engine = object()

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return engine

    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3306/d")
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)
    monkeypatch.delenv("DB_POOL_RECYCLE", raising=False)
    monkeypatch.delenv("DB_POOL_PRE_PING", raising=False)
    monkeypatch.setattr(storage_db, "_engine", None)
    monkeypatch.setattr(storage_db, "_Session", None)
    monkeypatch.setattr(storage_db, "create_engine", fake_create_engine)
    monkeypatch.setattr(storage_db.Base.metadata, "create_all", lambda bind: captured.setdefault("bind", bind))
    monkeypatch.setattr(storage_db, "sessionmaker", lambda **kwargs: {"sessionmaker": kwargs})

    assert storage_db.init_db() is True
    assert captured["url"] == "mysql+pymysql://u:p@h:3306/d"
    assert captured["bind"] is engine
    assert captured["kwargs"] == {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "isolation_level": "READ COMMITTED",
        "future": True,
    }
    assert storage_db._Session == {"sessionmaker": {"bind": engine, "future": True}}


def test_init_db_engine_options_can_be_overridden(monkeypatch):
    import storage.db as storage_db

    captured = {}

    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3306/d")
    monkeypatch.setenv("DB_POOL_SIZE", "8")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "16")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "45")
    monkeypatch.setenv("DB_POOL_RECYCLE", "1200")
    monkeypatch.setenv("DB_POOL_PRE_PING", "false")
    monkeypatch.setattr(storage_db, "_engine", None)
    monkeypatch.setattr(storage_db, "_Session", None)
    monkeypatch.setattr(storage_db, "create_engine", lambda url, **kwargs: captured.setdefault("kwargs", kwargs))
    monkeypatch.setattr(storage_db.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(storage_db, "sessionmaker", lambda **kwargs: object())

    assert storage_db.init_db() is True
    assert captured["kwargs"] == {
        "pool_size": 8,
        "max_overflow": 16,
        "pool_timeout": 45,
        "pool_recycle": 1200,
        "pool_pre_ping": False,
        "isolation_level": "READ COMMITTED",
        "future": True,
    }


def test_init_db_invalid_engine_options_fall_back_to_defaults(monkeypatch):
    import storage.db as storage_db

    captured = {}

    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@h:3306/d")
    monkeypatch.setenv("DB_POOL_SIZE", "bad")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "bad")
    monkeypatch.setenv("DB_POOL_RECYCLE", "bad")
    monkeypatch.setenv("DB_POOL_PRE_PING", "sometimes")
    monkeypatch.setattr(storage_db, "_engine", None)
    monkeypatch.setattr(storage_db, "_Session", None)
    monkeypatch.setattr(storage_db, "create_engine", lambda url, **kwargs: captured.setdefault("kwargs", kwargs))
    monkeypatch.setattr(storage_db.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(storage_db, "sessionmaker", lambda **kwargs: object())

    assert storage_db.init_db() is True
    assert captured["kwargs"] == {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "isolation_level": "READ COMMITTED",
        "future": True,
    }


def test_create_and_get_messages(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "标题")
    db.append_messages("c1", "userA", [
        {"role": "user", "type": "text", "content": "你好"},
        {"role": "assistant", "type": "thinking", "content": "让我想想"},
        {"role": "assistant", "type": "tool", "content": "42", "extra": {"name": "python_repl"}},
        {"role": "assistant", "type": "text", "content": "答案是42"},
    ])
    msgs = db.get_messages("c1", "userA")
    assert [(m["seq"], m["role"], m["type"]) for m in msgs] == [
        (1, "user", "text"), (2, "assistant", "thinking"),
        (3, "assistant", "tool"), (4, "assistant", "text"),
    ]
    tool = next(m for m in msgs if m["type"] == "tool")
    assert tool["extra"] == {"name": "python_repl"}


def test_append_records_user_id(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    db.append_messages("c1", "userA", [{"role": "user", "type": "text", "content": "hi"}])
    from storage.chat_message import ChatMessage
    from storage.db import _Session
    with _Session() as s:
        rows = s.query(ChatMessage).filter(ChatMessage.session_id == "c1").all()
    assert rows and all(r.user_id == "userA" for r in rows)


def test_owner_isolation_on_read(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    db.append_messages("c1", "userA", [{"role": "user", "type": "text", "content": "x"}])
    assert db.get_messages("c1", "userB") is None  # 非 owner 读不到
    assert db.get_owner("c1") == "userA"


def test_list_conversations_per_user_desc(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "一")
    db.create_conversation("c2", "userA", "二")
    db.create_conversation("cB", "userB", "别人")
    ids = [c["id"] for c in db.list_conversations("userA")]
    assert set(ids) == {"c1", "c2"} and "cB" not in ids


def test_list_conversations_filters_by_update_time_range(db_rollback):
    db = db_rollback
    db.create_conversation("old", "userA", "旧")
    db.create_conversation("start", "userA", "开始")
    db.create_conversation("inside", "userA", "中间")
    db.create_conversation("end", "userA", "结束")
    db.create_conversation("future", "userA", "未来")
    db.create_conversation("pinned_old", "userA", "置顶旧")
    db.create_conversation("pinned_future", "userA", "置顶未来")
    db.create_conversation("other", "userB", "别人")
    db.create_conversation("other_pinned", "userB", "别人置顶")

    from storage.chat_session import ChatSession
    from storage.db import _Session
    times = {
        "old": 900,
        "start": 1000,
        "inside": 1500,
        "end": 2000,
        "future": 2100,
        "pinned_old": 800,
        "pinned_future": 2200,
        "other": 1500,
        "other_pinned": 2200,
    }
    with _Session() as s, s.begin():
        for sid, update_time in times.items():
            conv = s.get(ChatSession, sid)
            conv.update_time = update_time
            if sid.startswith("pinned") or sid == "other_pinned":
                conv.top = True

    ids = [c["id"] for c in db.list_conversations("userA", start_time=1000, end_time=2000)]
    assert ids == ["pinned_future", "pinned_old", "end", "inside", "start"]
    assert [c["id"] for c in db.list_conversations("userA", start_time=2001, end_time=2000)] == [
        "pinned_future",
        "pinned_old",
    ]


def test_list_conversations_default_end_time_is_now(db_rollback, monkeypatch):
    db = db_rollback
    db.create_conversation("now", "userA", "现在")
    db.create_conversation("future", "userA", "未来")

    import storage.chat_session as chat_session
    from storage.chat_session import ChatSession
    from storage.db import _Session

    with _Session() as s, s.begin():
        s.get(ChatSession, "now").update_time = 2000
        s.get(ChatSession, "future").update_time = 3000
    monkeypatch.setattr(chat_session, "_now_ms", lambda: 2000)

    assert [c["id"] for c in db.list_conversations("userA")] == ["now"]


def test_update_title(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "旧")
    db.update_title("c1", "新标题")
    assert db.list_conversations("userA")[0]["title"] == "新标题"


def test_add_session_usage_accumulates(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    db.add_session_usage("c1", 100, 50, 150)
    db.add_session_usage("c1", 10, 5, 15)
    db.add_session_usage("c1", 0, 0, 0)      # 全 0 → no-op
    db.add_session_usage("nope", 1, 1, 1)    # 不存在 → no-op
    from storage.chat_session import ChatSession
    from storage.db import _Session
    with _Session() as s:
        c = s.get(ChatSession, "c1")
    assert (c.input_tokens, c.output_tokens, c.total_tokens) == (110, 55, 165)


def test_user_token_stats_total_and_daily(db_rollback):
    db = db_rollback
    import time
    db.create_conversation("c1", "userA", "t1")
    db.create_conversation("c2", "userA", "t2")
    # 把 c2 的创建日挪到另一天，验证 daily 分桶
    from storage.chat_session import ChatSession
    from storage.db import _Session
    with _Session() as s, s.begin():
        s.get(ChatSession, "c2").create_time = int(
            time.mktime(time.strptime("2026-07-01", "%Y-%m-%d"))
        ) * 1000
    db.add_session_usage("c1", 100, 50, 150)
    db.add_session_usage("c2", 200, 100, 300)

    stats = db.user_token_stats("userA")
    assert stats["total"] == {"input_tokens": 300, "output_tokens": 150, "total_tokens": 450}
    by_day = {d["date"]: d["total_tokens"] for d in stats["daily"]}
    assert by_day["2026-07-01"] == 300
    assert sum(by_day.values()) == 450


def test_delete_conversation_owner_checked(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    db.append_messages("c1", "userA", [{"role": "user", "type": "text", "content": "x"}])
    assert db.delete_conversation("c1", "userB") is False   # 非 owner
    assert db.delete_conversation("c1", "userA") is True
    assert db.get_owner("c1") is None
    assert db.get_messages("c1", "userA") is None


def test_delete_user_history(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t1")
    db.append_messages("c1", "userA", [{"role": "user", "type": "text", "content": "a"}])
    db.create_conversation("c2", "userA", "t2")
    db.append_messages("c2", "userA", [{"role": "user", "type": "text", "content": "b"}])
    db.create_conversation("cB", "userB", "tb")
    db.append_messages("cB", "userB", [{"role": "user", "type": "text", "content": "c"}])

    deleted = db.delete_user_history("userA")
    assert sorted(deleted) == ["c1", "c2"]
    assert db.list_conversations("userA") == []
    # userB 不受影响
    assert len(db.list_conversations("userB")) == 1
    from storage.chat_message import ChatMessage
    from storage.db import _Session
    with _Session() as s:
        assert s.query(ChatMessage).filter(ChatMessage.user_id == "userA").count() == 0
        assert s.query(ChatMessage).filter(ChatMessage.user_id == "userB").count() == 1


def test_chat_summary_insert_update_and_incremental_restore(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    seqs = db.append_messages("c1", "userA", [
        {"role": "user", "type": "text", "content": "u1"},
        {"role": "assistant", "type": "text", "content": "a1"},
        {"role": "user", "type": "text", "content": "u2"},
        {"role": "assistant", "type": "text", "content": "a2"},
        {"role": "user", "type": "text", "content": "tail-u"},
        {"role": "assistant", "type": "text", "content": "tail-a"},
    ])
    assert seqs == [1, 2, 3, 4, 5, 6]
    first = {
        "summary": {"goals": ["g"]},
        "covered_from_seq": 1,
        "covered_through_seq": 2,
        "covered_message_count": 2,
        "summary_version": 1,
        "estimated_tokens": 5,
    }
    assert db.save_chat_summary("c1", "userA", first) is True
    assert db.save_chat_summary("c1", "userB", first) is False
    assert db.load_memory_state("c1", "userB") is None

    from storage.chat_summary import ChatSummary
    from storage.db import _Session
    with _Session() as s:
        first_id = s.query(ChatSummary).filter(ChatSummary.session_id == "c1").one().id

    second = {**first, "covered_through_seq": 4, "covered_message_count": 4}
    assert db.save_chat_summary("c1", "userA", second) is True
    with _Session() as s:
        row = s.query(ChatSummary).filter(ChatSummary.session_id == "c1").one()
        assert row.id == first_id
        assert row.covered_through_seq == 4

    state = db.load_memory_state("c1", "userA")
    assert state["snapshot"]["covered_through_seq"] == 4
    assert [message["seq"] for message in state["messages"]] == [5, 6]


def test_chat_summary_monotonic_update_and_duplicate_repair(db_rollback):
    db = db_rollback
    db.create_conversation("c1", "userA", "t")
    db.append_messages("c1", "userA", [
        {"role": "user", "type": "text", "content": "u"},
        {"role": "assistant", "type": "text", "content": "a"},
    ])
    newer = {
        "summary": {"key_facts": ["new"]},
        "covered_from_seq": 1,
        "covered_through_seq": 2,
        "covered_message_count": 2,
        "summary_version": 1,
        "estimated_tokens": 5,
    }
    assert db.save_chat_summary("c1", "userA", newer) is True
    stale = {**newer, "summary": {"key_facts": ["stale"]}, "covered_through_seq": 1}
    assert db.save_chat_summary("c1", "userA", stale) is True

    from storage.chat_summary import ChatSummary
    from storage.db import _Session, _now_ms
    with _Session() as s, s.begin():
        s.add(ChatSummary(
            session_id="c1", user_id="userA", summary={"key_facts": ["duplicate"]},
            covered_from_seq=1, covered_through_seq=1, covered_message_count=1,
            summary_version=1, estimated_tokens=1,
            create_time=_now_ms(), update_time=_now_ms(),
        ))
    assert db.save_chat_summary("c1", "userA", newer) is True
    with _Session() as s:
        rows = s.query(ChatSummary).filter(ChatSummary.session_id == "c1").all()
        assert len(rows) == 1
        assert rows[0].summary == {"key_facts": ["new"]}


def test_chat_summary_deleted_with_conversation_and_user_history(db_rollback):
    db = db_rollback
    snapshot = {
        "summary": {"goals": ["g"]}, "covered_from_seq": 1,
        "covered_through_seq": 1, "covered_message_count": 1,
        "summary_version": 1, "estimated_tokens": 1,
    }
    for sid in ("c1", "c2"):
        db.create_conversation(sid, "userA", "t")
        db.append_messages(sid, "userA", [{"role": "user", "type": "text", "content": "u"}])
        assert db.save_chat_summary(sid, "userA", snapshot) is True

    from storage.chat_summary import ChatSummary
    from storage.db import _Session
    assert db.delete_conversation("c1", "userA") is True
    with _Session() as s:
        assert s.query(ChatSummary).filter(ChatSummary.session_id == "c1").count() == 0
    db.delete_user_history("userA")
    with _Session() as s:
        assert s.query(ChatSummary).filter(ChatSummary.user_id == "userA").count() == 0
