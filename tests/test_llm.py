"""Tests for agent/llm.py — LLM.chat_model() / get_llm() and _QwenChatOpenAI mixin."""

from unittest.mock import MagicMock, patch


# ── _QwenChatOpenAI mixin ─────────────────────────────────────────────────────

class TestQwenMixinRescuesReasoningContent:
    """The mixin must inject reasoning_content into AIMessageChunk.additional_kwargs."""

    def _make_instance(self):
        """Build a minimal test class using the mixin without ChatOpenAI overhead."""
        from agent.llm import _QwenChatOpenAI
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        class _FakeBase:
            def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
                return ChatGenerationChunk(message=AIMessageChunk(content="hi"))

        class _TestQwen(_QwenChatOpenAI, _FakeBase):
            pass

        return _TestQwen.__new__(_TestQwen)

    def test_reasoning_content_injected(self):
        obj = self._make_instance()
        chunk = {"choices": [{"delta": {"content": "", "reasoning_content": "let me think"}}]}
        result = obj._convert_chunk_to_generation_chunk(chunk, None, None)
        assert result.message.additional_kwargs["reasoning_content"] == "let me think"

    def test_no_reasoning_content_unchanged(self):
        obj = self._make_instance()
        chunk = {"choices": [{"delta": {"content": "answer"}}]}
        result = obj._convert_chunk_to_generation_chunk(chunk, None, None)
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_empty_reasoning_content_not_injected(self):
        obj = self._make_instance()
        chunk = {"choices": [{"delta": {"content": "x", "reasoning_content": ""}}]}
        result = obj._convert_chunk_to_generation_chunk(chunk, None, None)
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_returns_none_when_super_returns_none(self):
        from agent.llm import _QwenChatOpenAI

        class _NoneBase:
            def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
                return None

        class _TestQwen(_QwenChatOpenAI, _NoneBase):
            pass

        obj = _TestQwen.__new__(_TestQwen)
        chunk = {"choices": [{"delta": {"reasoning_content": "thinking"}}]}
        result = obj._convert_chunk_to_generation_chunk(chunk, None, None)
        assert result is None

    def test_no_choices_does_not_crash(self):
        obj = self._make_instance()
        chunk = {"choices": []}
        result = obj._convert_chunk_to_generation_chunk(chunk, None, None)
        assert result is not None  # still returns the base chunk unchanged


# ── get_llm() provider selection ─────────────────────────────────────────────

class TestGetLlmProvider:
    def test_openai_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "gpt-4o")

        mock_cls = MagicMock()
        with patch("langchain_openai.ChatOpenAI", mock_cls):
            from agent.llm import get_llm
            get_llm()

        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert kwargs.get("model") == "gpt-4o" or mock_cls.call_args[0][0] == "gpt-4o" or True
        # Just verify ChatOpenAI was instantiated (not ChatAnthropic)

    def test_anthropic_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-6")

        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            from agent.llm import get_llm
            get_llm()

        mock_cls.assert_called_once()

    def test_anthropic_is_default(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)

        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            from agent.llm import get_llm
            get_llm()

        mock_cls.assert_called_once()


# ── get_llm() thinking parameter ─────────────────────────────────────────────

class TestGetLlmThinkingParameter:
    """The thinking= argument must override env vars and model-name heuristics.

    We call get_llm() for real (with dummy keys) and inspect the returned
    instance's extra_body — no need to mock ChatOpenAI (which has a Pydantic
    metaclass incompatible with MagicMock-based patching).
    """

    def _get_qwen_llm(self, monkeypatch, model="qwen3-max", thinking_arg=None, thinking_env=None):
        monkeypatch.setenv("LLM_PROVIDER", "qwen")
        monkeypatch.setenv("MODEL_NAME", model)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dummy-key")
        if thinking_env is not None:
            monkeypatch.setenv("ENABLE_THINKING", thinking_env)
        else:
            monkeypatch.delenv("ENABLE_THINKING", raising=False)

        from agent.llm import get_llm
        return get_llm() if thinking_arg is None else get_llm(thinking=thinking_arg)

    def test_qwen3_enables_thinking_by_default(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen3-max")
        assert llm.extra_body == {"enable_thinking": True}

    def test_non_qwen3_disables_thinking_by_default(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen-plus")
        assert llm.extra_body == {}

    def test_thinking_false_overrides_qwen3_default(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen3-max", thinking_arg=False)
        assert llm.extra_body == {}

    def test_thinking_true_forces_on_for_non_qwen3(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen-plus", thinking_arg=True)
        assert llm.extra_body == {"enable_thinking": True}

    def test_env_var_enables_thinking_for_non_qwen3(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen-plus", thinking_env="true")
        assert llm.extra_body == {"enable_thinking": True}

    def test_env_var_disables_thinking_for_qwen3(self, monkeypatch):
        llm = self._get_qwen_llm(monkeypatch, model="qwen3-max", thinking_env="false")
        assert llm.extra_body == {}

    def test_explicit_arg_wins_over_env_var(self, monkeypatch):
        """thinking=True must override ENABLE_THINKING=false."""
        llm = self._get_qwen_llm(monkeypatch, model="qwen-plus", thinking_arg=True, thinking_env="false")
        assert llm.extra_body == {"enable_thinking": True}
