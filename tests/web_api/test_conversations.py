"""Conversation history and title API tests."""

from unittest.mock import MagicMock, patch

from web_api import runtime


class TestGenerateTitle:
    def test_returns_llm_summary(self, client):
        fake_resp = MagicMock()
        fake_resp.content = "黄金价格查询"
        model = MagicMock()
        model.invoke.return_value = fake_resp
        with patch("web_api.conversations.LLM") as LLMcls:
            LLMcls.return_value.chat_model.return_value = model
            LLMcls.extract_text = staticmethod(lambda c: c)
            resp = client.post("/api/title", json={"message": "明天黄金价格是多少"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "黄金价格查询"

    def test_falls_back_to_truncation_on_error(self, client):
        with patch("web_api.conversations.LLM") as LLMcls:
            LLMcls.return_value.chat_model.side_effect = RuntimeError("boom")
            resp = client.post("/api/title", json={"message": "x" * 50})
        assert resp.status_code == 200
        assert resp.json()["title"] == "x" * 24

    def test_empty_message_returns_empty(self, client):
        resp = client.post("/api/title", json={"message": "   "})
        assert resp.status_code == 200
        assert resp.json()["title"] == ""


class TestConversationHistory:
    def test_rebuild_memory_uses_summary_and_uncovered_tail(self):
        from memory import MemorySnapshot

        memory = MagicMock()
        record = {"memory": memory}
        state = {
            "snapshot": {
                "summary": {"key_facts": ["saved"]},
                "covered_from_seq": 1,
                "covered_through_seq": 4,
                "covered_message_count": 4,
                "summary_version": 1,
                "estimated_tokens": 8,
            },
            "messages": [
                {"seq": 5, "role": "user", "type": "text", "content": "tail"},
            ],
        }
        with patch.object(runtime.db, "load_memory_state", return_value=state):
            count = runtime.rebuild_memory(record, "c1", "userA")

        assert count == 1
        snapshot, messages = memory.restore.call_args.args
        assert isinstance(snapshot, MemorySnapshot)
        assert snapshot.covered_through_seq == 4
        assert messages == state["messages"]

    def test_rebuild_memory_falls_back_when_summary_storage_is_unavailable(self):

        memory = MagicMock()
        history = [
            {"seq": 1, "role": "user", "type": "text", "content": "full history"},
            {"seq": 2, "role": "assistant", "type": "tool", "content": "ignored"},
        ]
        with (
            patch.object(runtime.db, "load_memory_state", return_value=None),
            patch.object(runtime.db, "get_messages", return_value=history),
        ):
            count = runtime.rebuild_memory({"memory": memory}, "c1", "userA")

        assert count == 1
        memory.load_history.assert_called_once_with(history)

    def test_list_conversations_passes_time_range(self, client):

        with (
            patch("web_api.conversations.current_user_id", return_value="userA"),
            patch.object(runtime.db, "list_conversations", return_value=[
                {"id": "c1", "title": "t", "update_time": 2000, "top": False}
            ]) as list_conversations,
        ):
            resp = client.get("/api/conversations?start_time=1000&end_time=2000")

        assert resp.status_code == 200
        assert resp.json()["conversations"] == [
            {"id": "c1", "title": "t", "update_time": 2000, "top": False}
        ]
        list_conversations.assert_called_once_with("userA", start_time=1000, end_time=2000)

