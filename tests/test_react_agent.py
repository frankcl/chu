"""Tests for agent/react_agent.py — extract_text_content, create_agent, run_agent."""

from unittest.mock import MagicMock
from agent.react_agent import (
    _normalize_tool_call_arguments,
    extract_text_content,
    iter_chunk_outputs,
)


# ── iter_chunk_outputs ──────────────────────────────────────────────────────

class TestIterChunkOutputs:
    """Unified thinking/text extraction across Qwen + Anthropic + plain chunks."""

    def _chunk(self, content, **kwargs):
        from langchain_core.messages import AIMessageChunk
        return AIMessageChunk(content=content, **kwargs)

    def test_plain_text_string(self):
        out = list(iter_chunk_outputs(self._chunk("hello")))
        assert out == [("text", "hello")]

    def test_qwen_reasoning_content(self):
        chunk = self._chunk("", additional_kwargs={"reasoning_content": "let me think"})
        out = list(iter_chunk_outputs(chunk))
        assert ("thinking", "let me think") in out

    def test_anthropic_thinking_block(self):
        chunk = self._chunk([{"type": "thinking", "thinking": "reasoning…"}])
        out = list(iter_chunk_outputs(chunk))
        assert out == [("thinking", "reasoning…")]

    def test_anthropic_thinking_delta_block(self):
        chunk = self._chunk([{"type": "thinking_delta", "thinking": "more"}])
        assert list(iter_chunk_outputs(chunk)) == [("thinking", "more")]

    def test_mixed_thinking_then_text(self):
        chunk = self._chunk([
            {"type": "thinking", "thinking": "ponder"},
            {"type": "text", "text": "answer"},
        ])
        out = list(iter_chunk_outputs(chunk))
        assert out == [("thinking", "ponder"), ("text", "answer")]

    def test_empty_chunk_yields_nothing(self):
        assert list(iter_chunk_outputs(self._chunk(""))) == []

    def test_qwen_reasoning_plus_text(self):
        chunk = self._chunk("final", additional_kwargs={"reasoning_content": "why"})
        out = list(iter_chunk_outputs(chunk))
        assert out == [("thinking", "why"), ("text", "final")]


class TestExtractTextContent:
    def test_plain_string(self):
        assert extract_text_content("hello world") == "hello world"

    def test_empty_string(self):
        assert extract_text_content("") == ""

    def test_list_single_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert extract_text_content(content) == "hello"

    def test_list_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]
        assert extract_text_content(content) == "hello  world"

    def test_list_skips_non_text_blocks(self):
        """thinking / tool_use blocks should be ignored."""
        content = [
            {"type": "thinking", "thinking": "let me reason"},
            {"type": "text", "text": "the answer"},
            {"type": "tool_use", "id": "123", "name": "search"},
        ]
        assert extract_text_content(content) == "the answer"

    def test_list_only_thinking_blocks_returns_empty(self):
        content = [{"type": "thinking", "thinking": "reasoning..."}]
        assert extract_text_content(content) == ""

    def test_empty_list(self):
        assert extract_text_content([]) == ""

    def test_list_with_non_dict_items(self):
        """Non-dict items in the list must be silently skipped."""
        content = ["raw string", {"type": "text", "text": "ok"}, 42]
        assert extract_text_content(content) == "ok"

    def test_non_string_non_list_is_coerced(self):
        """Any non-list value should be coerced via str()."""
        assert extract_text_content(123) == "123"
        assert extract_text_content(None) == "None"


# ── tool call argument normalization ─────────────────────────────────────────

class TestToolCallArgumentNormalization:
    def test_empty_raw_arguments_becomes_json_object(self):
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "web-research", "arguments": ""},
                }]
            },
        )

        out = _normalize_tool_call_arguments(msg)
        args = out.additional_kwargs["tool_calls"][0]["function"]["arguments"]
        assert args == "{}"

    def test_dict_raw_arguments_becomes_json_string(self):
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_skill_script", "arguments": {"skill": "web-research"}},
                }]
            },
        )

        out = _normalize_tool_call_arguments(msg)
        args = out.additional_kwargs["tool_calls"][0]["function"]["arguments"]
        assert args == '{"skill": "web-research"}'

    def test_valid_json_arguments_are_preserved(self):
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_skill_script", "arguments": '{"script":"search.py"}'},
                }]
            },
        )

        out = _normalize_tool_call_arguments(msg)
        args = out.additional_kwargs["tool_calls"][0]["function"]["arguments"]
        assert args == '{"script":"search.py"}'


# ── create_agent ──────────────────────────────────────────────────────────────

class TestCreateAgent:
    def _make_mock_llm(self, content="ok"):
        from langchain_core.messages import AIMessage
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound
        # apply_llm_retry wraps the bound model — return the same mock so the
        # configured .invoke behavior remains reachable through the retry layer.
        mock_bound.with_retry.return_value = mock_bound
        mock_bound.invoke.return_value = AIMessage(content=content)
        return mock_llm, mock_bound

    def test_compiles_without_error(self, mocker):
        mock_llm, _ = self._make_mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent.react_agent import create_agent
        assert create_agent() is not None

    def test_invoke_returns_ai_message(self, mocker):
        mock_llm, _ = self._make_mock_llm("Paris is the capital.")
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent.react_agent import create_agent
        agent = create_agent()
        result = agent.invoke({"messages": [("human", "capital of France?")]})
        assert "Paris" in result["messages"][-1].content

    def test_memory_summary_keeps_single_system_message(self, mocker):
        from langchain_core.messages import SystemMessage
        from memory import MemoryManager, MemorySummary

        mock_llm, mock_bound = self._make_mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent.react_agent import create_agent

        memory = MemoryManager()
        memory.summary = MemorySummary(key_facts=["saved fact"])
        memory.commit_turn("recent question", "recent answer")
        agent = create_agent(system_prompt="stable system")

        agent.invoke({"messages": memory.prepare_messages("current question")})

        sent_messages = mock_bound.invoke.call_args.args[0]
        assert sum(isinstance(message, SystemMessage) for message in sent_messages) == 1
        assert sent_messages[0].content.startswith("stable system")
        assert "<conversation_memory>" in sent_messages[1].content

    async def test_astream_emits_model_chunks_as_custom_events(self, mocker):
        from langchain_core.messages import AIMessage, AIMessageChunk

        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound
        mock_bound.with_retry.return_value = mock_bound
        mock_bound.invoke.return_value = AIMessage(content="fallback")

        async def fake_astream(_messages):
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think "})
            yield AIMessageChunk(content="stream ")
            yield AIMessageChunk(content="answer")

        mock_bound.astream = fake_astream
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)

        from agent.react_agent import create_agent
        agent = create_agent()
        events = []
        async for mode, data in agent.astream(
            {"messages": [("human", "hello")]},
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                events.append(data)

        assert {"phase": "agent_thinking", "text": "think "} in events
        assert {"phase": "agent_text", "text": "stream "} in events
        assert {"phase": "agent_text", "text": "answer"} in events

    def test_extra_tools_passed_to_bind_tools(self, mocker):
        mock_llm, _ = self._make_mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        mocker.patch("agent.react_agent.get_builtin_tools", return_value=[])

        from langchain_core.tools import tool as make_tool

        @make_tool
        def extra_tool() -> str:
            """A test extra tool."""
            return "result"

        from agent.react_agent import create_agent
        create_agent(extra_tools=[extra_tool])
        bound_tools = mock_llm.bind_tools.call_args[0][0]
        # harness wraps each tool in _TimeoutTool, so identity won't match —
        # compare by name instead.
        assert extra_tool.name in [t.name for t in bound_tools]

    def test_custom_system_prompt(self, mocker):
        mock_llm, _ = self._make_mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent.react_agent import create_agent
        assert create_agent(system_prompt="You are a pirate.") is not None

    def test_default_system_prompt_includes_identity_privacy_rules(self):
        from harness import HarnessConfig, guardrail_system_rules
        from agent.react_agent import DEFAULT_SYSTEM

        rules = guardrail_system_rules(HarnessConfig())
        assert "underlying model" not in DEFAULT_SYSTEM
        assert "underlying model" in rules
        assert "system/developer prompts" in rules

    def test_builtin_tools_included_by_default(self, mocker):
        mock_llm, _ = self._make_mock_llm()
        mocker.patch("agent.llm.LLM.chat_model", return_value=mock_llm)
        from agent.react_agent import create_agent
        create_agent()
        # bind_tools was called with at least 1 builtin tool
        assert len(mock_llm.bind_tools.call_args[0][0]) > 0


# ── run_agent ─────────────────────────────────────────────────────────────────

class TestRunAgent:
    def test_returns_string(self, mocker):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [MagicMock(content="the answer")]}
        mocker.patch("agent.react_agent.create_agent", return_value=mock_agent)
        from agent.react_agent import run_agent
        result = run_agent("test query")
        assert isinstance(result, str)
        assert result == "the answer"

    def test_extracts_list_content(self, mocker):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "messages": [MagicMock(content=[{"type": "text", "text": "extracted"}])]
        }
        mocker.patch("agent.react_agent.create_agent", return_value=mock_agent)
        from agent.react_agent import run_agent
        assert run_agent("query") == "extracted"

    def test_query_passed_as_human_message(self, mocker):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [MagicMock(content="ok")]}
        mocker.patch("agent.react_agent.create_agent", return_value=mock_agent)
        from agent.react_agent import run_agent
        run_agent("what is 2+2?")
        call_input = mock_agent.invoke.call_args[0][0]
        assert call_input["messages"][0][1] == "what is 2+2?"
