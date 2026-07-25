import logging
from unittest.mock import MagicMock

import pytest
from web_api import runtime

from harness import HarnessConfig
from memory import MemoryManager, MemorySnapshot, MemorySummary


class _SummaryRunnable:
    def __init__(self, result=None, error=None):
        self.result = result or MemorySummary(key_facts=["早期关键信息"])
        self.error = error
        self.calls = []

    def invoke(self, values, config=None):
        self.calls.append((values, config))
        if self.error:
            raise self.error
        return self.result

    async def ainvoke(self, values, config=None):
        return self.invoke(values, config)


def _config(**overrides):
    return HarnessConfig(
        memory_max_tokens=overrides.get("memory_max_tokens", 500),
        memory_target_tokens=overrides.get("memory_target_tokens", 300),
        memory_keep_recent_turns=overrides.get("memory_keep_recent_turns", 2),
    )


def test_default_memory_watermarks(monkeypatch):
    monkeypatch.delenv("MEMORY_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MEMORY_TARGET_TOKENS", raising=False)

    config = HarnessConfig.from_env()

    assert config.memory_max_tokens == 24_000
    assert config.memory_target_tokens == 12_000


def test_memory_package_exports_public_types_without_agent_reexport():
    import agent
    from memory import MemoryTurn

    assert MemoryManager is not None
    assert MemorySummary is not None
    assert MemoryTurn is not None
    assert not hasattr(agent, "MemoryManager")


def test_small_memory_is_returned_without_summarization():
    manager = MemoryManager(_config())
    manager.commit_turn("你好", "你好，有什么可以帮你？")

    messages = manager.prepare_messages("继续")

    assert [message.type for message in messages] == ["human", "ai", "human"]
    assert messages[-1].content == "继续"
    assert manager.summary.is_empty()


def test_small_memory_does_not_log_compaction(caplog):
    manager = MemoryManager(_config())
    manager.commit_turn("你好", "你好，有什么可以帮你？")

    with caplog.at_level(logging.INFO, logger="memory"):
        compacted = manager.compact("继续")

    assert compacted is False
    assert "memory compact starting" not in caplog.text


def test_sync_compaction_logs_tokens_before_and_actual_tokens_after(caplog):
    manager = MemoryManager(_config())
    for index in range(6):
        manager.commit_turn(f"用户{index}:" + "甲" * 120, f"回答{index}:" + "乙" * 120)
    manager._summary_runnable = lambda: _SummaryRunnable()
    tokens_before = manager.estimate_tokens("新问题")

    with caplog.at_level(logging.INFO, logger="memory"):
        compacted = manager.compact("新问题")

    tokens_after = manager.estimate_tokens("新问题")
    assert compacted is True
    assert f"memory compact starting tokens_before={tokens_before}" in caplog.text
    assert (
        f"memory compact completed tokens_before={tokens_before} tokens_after={tokens_after}"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_large_memory_summarizes_old_turns_and_keeps_recent(caplog):
    manager = MemoryManager(_config())
    for index in range(6):
        manager.commit_turn(f"用户{index}:" + "甲" * 120, f"回答{index}:" + "乙" * 120)
    runnable = _SummaryRunnable()
    manager._summary_runnable = lambda: runnable
    tokens_before = manager.estimate_tokens("新问题")

    with caplog.at_level(logging.INFO, logger="memory"):
        messages = await manager.aprepare_messages("新问题")

    assert runnable.calls
    assert f"memory compact starting tokens_before={tokens_before}" in caplog.text
    assert manager.summary.key_facts == ["早期关键信息"]
    assert len(manager.turns) <= 2
    assert messages[0].type == "human"
    assert messages[0].content.startswith("<conversation_memory>\n")
    assert "早期关键信息" in messages[0].content
    assert messages[-1].content == "新问题"
    assert manager.estimate_tokens("新问题") <= manager.config.memory_target_tokens


def test_summary_is_prepended_to_first_recent_user_without_mutating_turn():
    manager = MemoryManager(_config())
    manager.summary = MemorySummary(key_facts=["较早事实"])
    manager.commit_turn("近期问题", "近期回答")

    first = manager.prepare_messages("当前问题")
    second = manager.prepare_messages("另一个问题")

    assert [message.type for message in first] == ["human", "ai", "human"]
    assert first[0].content.startswith("<conversation_memory>\n")
    assert first[0].content.endswith("\n\n近期问题")
    assert first[-1].content == "当前问题"
    assert second[0].content.count("<conversation_memory>") == 1
    assert manager.turns[0].user == "近期问题"


def test_summary_is_prepended_to_current_user_when_no_recent_user_exists():
    manager = MemoryManager(_config())
    manager.summary = MemorySummary(key_facts=["较早事实"])

    messages = manager.prepare_messages("当前问题")

    assert [message.type for message in messages] == ["human"]
    assert messages[0].content.startswith("<conversation_memory>\n")
    assert messages[0].content.endswith("\n\n当前问题")


@pytest.mark.asyncio
async def test_summary_failure_falls_back_to_a_hard_bound(caplog):
    manager = MemoryManager(_config(memory_max_tokens=200, memory_target_tokens=100))
    for index in range(5):
        manager.commit_turn(str(index) + "甲" * 100, "乙" * 100)
    manager._summary_runnable = lambda: _SummaryRunnable(error=RuntimeError("provider down"))
    tokens_before = manager.estimate_tokens("当前问题")

    with caplog.at_level(logging.INFO, logger="memory"):
        await manager.acompact("当前问题")

    assert f"memory compact starting tokens_before={tokens_before}" in caplog.text
    assert "memory summary failed; applying deterministic trim" in caplog.text
    assert manager.summary.is_empty()
    assert len(manager.turns) <= 1
    assert manager.estimate_tokens("当前问题") <= manager.config.memory_target_tokens + 20


def test_history_rebuild_ignores_non_text_rows():
    manager = MemoryManager(_config())
    manager.load_history([
        {"seq": 1, "role": "user", "type": "text", "content": "问题"},
        {"seq": 2, "role": "assistant", "type": "tool", "content": "huge raw tool result"},
        {"seq": 3, "role": "assistant", "type": "text", "content": "答案"},
    ])

    assert len(manager.turns) == 1
    assert manager.turns[0].user == "问题"
    assert manager.turns[0].assistant == "答案"
    assert manager.turns[0].source_seqs == [1, 3]


@pytest.mark.asyncio
async def test_successful_compaction_produces_incremental_snapshot():
    manager = MemoryManager(_config())
    rows = []
    for index in range(6):
        rows.extend([
            {"seq": index * 2 + 1, "role": "user", "type": "text", "content": "甲" * 120},
            {"seq": index * 2 + 2, "role": "assistant", "type": "text", "content": "乙" * 120},
        ])
    manager.load_history(rows)
    manager._summary_runnable = lambda: _SummaryRunnable()

    await manager.acompact("新问题")

    snapshot = manager.pending_snapshot()
    assert isinstance(snapshot, MemorySnapshot)
    assert snapshot.covered_from_seq == 1
    assert snapshot.covered_through_seq == 8
    assert snapshot.covered_message_count == 8
    manager.mark_snapshot_persisted(snapshot.covered_through_seq)
    assert manager.pending_snapshot() is None


def test_restore_uses_summary_and_tail_source_sequences():
    snapshot = MemorySnapshot(
        summary=MemorySummary(key_facts=["已持久化事实"]),
        covered_from_seq=1,
        covered_through_seq=4,
        covered_message_count=4,
        estimated_tokens=10,
    )
    manager = MemoryManager(_config())
    manager.restore(snapshot, [
        {"seq": 5, "role": "user", "type": "text", "content": "新问题"},
        {"seq": 6, "role": "assistant", "type": "text", "content": "新回答"},
    ])

    assert manager.summary.key_facts == ["已持久化事实"]
    assert manager.turns[0].source_seqs == [5, 6]


@pytest.mark.parametrize("kwargs", [
    {"memory_max_tokens": 0},
    {"memory_max_tokens": 100, "memory_target_tokens": 100},
    {"memory_keep_recent_turns": 0},
    {"memory_ttl_seconds": 0},
])
def test_invalid_memory_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        HarnessConfig(**kwargs)


def test_runtime_eviction_skips_active_session():

    cfg = HarnessConfig(memory_ttl_seconds=10)
    expired = {
        "harness": cfg,
        "last_access_at": 0.0,
        "active_task": None,
        "hitl": MagicMock(is_pending=MagicMock(return_value=False)),
        "memory": MagicMock(),
    }
    active_task = MagicMock(done=MagicMock(return_value=False))
    active = {**expired, "active_task": active_task, "memory": MagicMock()}
    runtime.sessions.update({"expired": expired, "active": active})

    evicted = runtime.evict_idle_sessions(now=11.0)

    assert evicted == ["expired"]
    assert "expired" not in runtime.sessions
    assert "active" in runtime.sessions
