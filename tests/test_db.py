"""对话历史持久化层（agent.db）的测试。

全部用 `db_rollback` fixture（见 conftest.py）：每个用例在一个外层事务里跑，
内部各 helper 的 commit 只落到 SAVEPOINT，测试结束统一 rollback —— 不留任何数据。
"""


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
    from agent.db import ChatMessage, _Session
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
    from agent.db import ChatSession, _Session
    with _Session() as s:
        c = s.get(ChatSession, "c1")
    assert (c.input_tokens, c.output_tokens, c.total_tokens) == (110, 55, 165)


def test_user_token_stats_total_and_daily(db_rollback):
    db = db_rollback
    import time
    db.create_conversation("c1", "userA", "t1")
    db.create_conversation("c2", "userA", "t2")
    # 把 c2 的创建日挪到另一天，验证 daily 分桶
    from agent.db import ChatSession, _Session
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
    from agent.db import ChatMessage, _Session
    with _Session() as s:
        assert s.query(ChatMessage).filter(ChatMessage.user_id == "userA").count() == 0
        assert s.query(ChatMessage).filter(ChatMessage.user_id == "userB").count() == 1
