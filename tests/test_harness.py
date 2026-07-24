"""Tests for the harness control layer (harness package + server cancel endpoint)."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from web_api import auth, runtime

from harness import (
    BudgetExceededError,
    BudgetTracker,
    HarnessConfig,
    TaskBudgetExceededError,
    TaskBudgetTracker,
    apply_llm_retry,
    wrap_tools,
)


# ── HarnessConfig.from_env ───────────────────────────────────────────────────

class TestHarnessConfigFromEnv:
    def test_defaults_when_env_unset(self, monkeypatch):
        for key in (
            "RECURSION_LIMIT", "IDLE_TIMEOUT_SECONDS", "PER_TOOL_TIMEOUT_SECONDS",
            "MAX_TOOL_CALLS", "MAX_TOOL_CALLS_PER_TASK",
            "MAX_SKILL_SCRIPT_CALLS_PER_TASK", "MAX_TOKENS_BUDGET", "LLM_MAX_RETRIES",
            "TOOL_ALLOWLIST", "TOOL_DENYLIST", "ENABLED_GUARDRAILS",
            "SENSITIVE_OUTPUT_SCAN", "SENSITIVE_OUTPUT_ACTION",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = HarnessConfig.from_env()
        assert cfg.recursion_limit == 25
        assert cfg.idle_timeout == 60.0
        assert cfg.per_tool_timeout == 30.0
        assert cfg.max_tool_calls == 20
        assert cfg.max_tool_calls_per_task == 8
        assert cfg.max_skill_script_calls_per_task == 3
        assert cfg.max_tokens == 200_000
        assert cfg.llm_max_retries == 2
        assert cfg.tool_allowlist is None
        assert cfg.tool_denylist == []
        assert cfg.enabled_guardrails == ["identity_privacy", "safety"]
        assert cfg.sensitive_output_scan is True
        assert cfg.sensitive_output_action == "redact"

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("RECURSION_LIMIT", "7")
        monkeypatch.setenv("IDLE_TIMEOUT_SECONDS", "12.5")
        monkeypatch.setenv("MAX_TOOL_CALLS", "3")
        monkeypatch.setenv("MAX_TOOL_CALLS_PER_TASK", "4")
        monkeypatch.setenv("MAX_SKILL_SCRIPT_CALLS_PER_TASK", "2")
        monkeypatch.setenv("TOOL_DENYLIST", "python_repl, write_file")
        monkeypatch.setenv("TOOL_ALLOWLIST", "")
        monkeypatch.setenv("ENABLED_GUARDRAILS", "identity_privacy")
        monkeypatch.setenv("SENSITIVE_OUTPUT_SCAN", "false")
        monkeypatch.setenv("SENSITIVE_OUTPUT_ACTION", "block")
        cfg = HarnessConfig.from_env()
        assert cfg.recursion_limit == 7
        assert cfg.idle_timeout == 12.5
        assert cfg.max_tool_calls == 3
        assert cfg.max_tool_calls_per_task == 4
        assert cfg.max_skill_script_calls_per_task == 2
        assert cfg.tool_denylist == ["python_repl", "write_file"]
        assert cfg.tool_allowlist is None  # empty string → None (allow all)
        assert cfg.enabled_guardrails == ["identity_privacy"]
        assert cfg.sensitive_output_scan is False
        assert cfg.sensitive_output_action == "block"

    def test_invalid_sensitive_output_action_falls_back_to_redact(self):
        cfg = HarnessConfig(sensitive_output_action="unknown")
        assert cfg.sensitive_output_action == "redact"

    def test_garbage_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RECURSION_LIMIT", "not-a-number")
        cfg = HarnessConfig.from_env()
        assert cfg.recursion_limit == 25


# ── HarnessConfig.merge ──────────────────────────────────────────────────────

class TestHarnessConfigMerge:
    def test_none_does_not_overwrite(self):
        base = HarnessConfig(recursion_limit=10, max_tool_calls=5)
        merged = base.merge({"recursion_limit": None, "max_tool_calls": 99})
        assert merged.recursion_limit == 10
        assert merged.max_tool_calls == 99

    def test_unknown_keys_ignored(self):
        base = HarnessConfig()
        merged = base.merge({"unknown_field": 123, "recursion_limit": 50})
        assert merged.recursion_limit == 50
        assert not hasattr(merged, "unknown_field")

    def test_returns_new_instance(self):
        base = HarnessConfig(recursion_limit=10)
        merged = base.merge({"recursion_limit": 20})
        assert base.recursion_limit == 10  # unchanged
        assert merged.recursion_limit == 20


# ── BudgetTracker ────────────────────────────────────────────────────────────

def _make_llm_result(total_tokens: int) -> LLMResult:
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 0, "output_tokens": total_tokens, "total_tokens": total_tokens},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


class TestBudgetTracker:
    def test_tokens_accumulate(self):
        t = BudgetTracker(HarnessConfig(max_tokens=1000))
        t.on_llm_end(_make_llm_result(100))
        t.on_llm_end(_make_llm_result(200))
        assert t.total_tokens == 300

    def test_token_budget_exceeded_raises(self):
        t = BudgetTracker(HarnessConfig(max_tokens=150))
        t.on_llm_end(_make_llm_result(100))
        with pytest.raises(BudgetExceededError) as ei:
            t.on_llm_end(_make_llm_result(100))
        assert ei.value.reason == "token"
        assert "200" in ei.value.message

    def test_tool_call_budget_exceeded_raises(self):
        t = BudgetTracker(HarnessConfig(max_tool_calls=2))
        t.on_tool_start({}, "x")
        t.on_tool_start({}, "x")
        with pytest.raises(BudgetExceededError) as ei:
            t.on_tool_start({}, "x")
        assert ei.value.reason == "tool_calls"

    def test_missing_usage_metadata_is_tolerated(self):
        t = BudgetTracker(HarnessConfig(max_tokens=1000))
        msg = AIMessage(content="hi")  # no usage_metadata
        result = LLMResult(generations=[[ChatGeneration(message=msg)]])
        t.on_llm_end(result)
        assert t.total_tokens == 0


class TestTaskBudgetTracker:
    def test_tool_call_budget_exceeded_raises_task_error(self):
        t = TaskBudgetTracker("task-a", HarnessConfig(max_tool_calls_per_task=1))
        t.on_tool_start({}, "x")
        with pytest.raises(TaskBudgetExceededError) as ei:
            t.on_tool_start({}, "x")
        assert ei.value.task_id == "task-a"
        assert ei.value.reason == "tool_calls_per_task"


# ── wrap_tools (filter + timeout) ────────────────────────────────────────────

@tool
def fast_tool(x: str = "") -> str:
    """A fast no-op tool."""
    return f"fast:{x}"


@tool
def slow_tool(x: str = "") -> str:
    """A tool that sleeps longer than the timeout.

    Keep this sleep short — the inner thread keeps running after the
    timeout returns, so anything longer just stretches process exit time.
    """
    time.sleep(0.4)
    return f"slow:{x}"


class TestWrapTools:
    def test_denylist_excludes(self):
        cfg = HarnessConfig(tool_denylist=["slow_tool"], per_tool_timeout=5)
        out = wrap_tools([fast_tool, slow_tool], cfg)
        assert [t.name for t in out] == ["fast_tool"]

    def test_allowlist_only_keeps_listed(self):
        cfg = HarnessConfig(tool_allowlist=["fast_tool"], per_tool_timeout=5)
        out = wrap_tools([fast_tool, slow_tool], cfg)
        assert [t.name for t in out] == ["fast_tool"]

    def test_denylist_wins_over_allowlist(self):
        cfg = HarnessConfig(
            tool_allowlist=["fast_tool", "slow_tool"],
            tool_denylist=["slow_tool"],
            per_tool_timeout=5,
        )
        out = wrap_tools([fast_tool, slow_tool], cfg)
        assert [t.name for t in out] == ["fast_tool"]

    def test_timeout_returns_string_not_raises(self):
        cfg = HarnessConfig(per_tool_timeout=0.2)
        out = wrap_tools([slow_tool], cfg)
        wrapped = out[0]
        result = wrapped.invoke({"x": "hi"})
        assert "tool timeout" in result
        assert "slow_tool" in result

    def test_fast_tool_passes_through(self):
        cfg = HarnessConfig(per_tool_timeout=5)
        out = wrap_tools([fast_tool], cfg)
        assert out[0].invoke({"x": "ok"}) == "fast:ok"


# ── apply_llm_retry ──────────────────────────────────────────────────────────

class TestApplyLlmRetry:
    def test_zero_retries_returns_original(self):
        llm = MagicMock()
        cfg = HarnessConfig(llm_max_retries=0)
        assert apply_llm_retry(llm, cfg) is llm

    def test_positive_retries_calls_with_retry(self):
        llm = MagicMock()
        wrapped = MagicMock()
        llm.with_retry.return_value = wrapped
        cfg = HarnessConfig(llm_max_retries=3)
        out = apply_llm_retry(llm, cfg)
        assert out is wrapped
        llm.with_retry.assert_called_once()
        kwargs = llm.with_retry.call_args.kwargs
        assert kwargs["stop_after_attempt"] == 4  # retries + 1
        assert kwargs["wait_exponential_jitter"] is True


# ── regression: bind_tools / with_structured_output must precede retry ──────

class TestAgentBuildOrder:
    """Original bug: `apply_llm_retry(get_llm()).bind_tools(...)` raised
    `RunnableRetry object has no attribute 'bind_tools'`. MagicMock can't
    catch this because it auto-generates any attribute. So we assert the
    real RunnableRetry's API directly — no heavy SDK instantiation needed."""

    def test_runnable_retry_lacks_bind_tools(self):
        from langchain_core.runnables import RunnableLambda
        fake = RunnableLambda(lambda x: x)
        cfg = HarnessConfig(llm_max_retries=1)
        retried = apply_llm_retry(fake, cfg)
        # If this assertion ever flips, langchain has added bind_tools to
        # RunnableRetry and the ordering constraint in graph.py is moot.
        assert not hasattr(retried, "bind_tools")
        assert not hasattr(retried, "with_structured_output")


# ── shared FastAPI client (module-scoped to keep memory flat) ────────────────

@pytest.fixture(scope="module")
def client():
    """One TestClient + patched agent constructors for the whole module."""
    from fastapi.testclient import TestClient

    mock_agent = MagicMock()

    async def _empty_astream(*args, **kwargs):
        return
        yield

    mock_agent.astream = _empty_astream
    with (
        patch("web_api.runtime.create_agent", return_value=mock_agent),
        patch("web_api.runtime.create_plan_execute_agent", return_value=mock_agent),
    ):
        import server
        from web_api.runtime import sessions
        # hylian shield (Bearer-first branch) protects /api/*: stub get_user + send a bearer token.
        with patch.object(auth.hylian_client, "get_user", return_value=MagicMock()):
            with TestClient(
                server.app,
                raise_server_exceptions=False,
                headers={"Authorization": "Bearer test-token"},
            ) as c:
                yield c
                sessions.clear()  # don't leak session dict across module tear-down


# ── server /cancel endpoint ──────────────────────────────────────────────────

class TestCancelEndpoint:
    def test_cancel_unknown_session_returns_404(self, client):
        resp = client.post("/api/chat/no-such-session/cancel")
        assert resp.status_code == 404

    def test_cancel_with_no_active_task(self, client):
        sid = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{sid}/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "cancelled": False}

    def test_cancel_with_active_task_cancels_it(self, client):
        """Inject a fake long-running task into the session and verify /cancel kills it."""

        sid = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]

        loop = asyncio.new_event_loop()

        async def long_task():
            await asyncio.sleep(60)

        try:
            task = loop.create_task(long_task())
            runtime.sessions[sid]["active_task"] = task
            loop.run_until_complete(asyncio.sleep(0))  # let task start

            resp = client.post(f"/api/chat/{sid}/cancel")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True, "cancelled": True}

            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            assert task.cancelled()
        finally:
            loop.close()


# ── session create with harness overrides ────────────────────────────────────

class TestSessionHarnessOverrides:
    def test_override_stored_on_session(self, client):
        resp = client.post("/api/sessions", json={
            "mode": "react",
            "max_tool_calls": 1,
            "max_tool_calls_per_task": 2,
            "max_skill_script_calls_per_task": 3,
            "idle_timeout": 5.0,
            "tool_denylist": ["python_repl"],
        })
        sid = resp.json()["session_id"]
        cfg = runtime.sessions[sid]["harness"]
        assert cfg.max_tool_calls == 1
        assert cfg.max_tool_calls_per_task == 2
        assert cfg.max_skill_script_calls_per_task == 3
        assert cfg.idle_timeout == 5.0
        assert "python_repl" in cfg.tool_denylist

    def test_none_overrides_keep_env_defaults(self, client):
        resp = client.post("/api/sessions", json={"mode": "react"})
        sid = resp.json()["session_id"]
        cfg = runtime.sessions[sid]["harness"]
        assert cfg.recursion_limit == 25


# ── idle timeout behaviour ───────────────────────────────────────────────────

class TestIdleTimeoutBehavior:
    """idle_timeout fires on inactivity, not total duration. A stream that
    keeps emitting events stays alive arbitrarily long."""

    def _client_with_agent(self, fake_astream):
        """Build a TestClient whose mocked agent uses the given astream."""
        from fastapi.testclient import TestClient

        mock_agent = MagicMock()
        mock_agent.astream = fake_astream
        ctx = (
            patch("web_api.runtime.create_agent", return_value=mock_agent),
            patch("web_api.runtime.create_plan_execute_agent", return_value=mock_agent),
        )
        for c in ctx:
            c.start()
        import server
        # hylian shield (Bearer-first branch) protects /api/*: stub get_user + send a bearer token.
        gu = patch.object(auth.hylian_client, "get_user", return_value=MagicMock())
        gu.start()
        client = TestClient(
            server.app,
            raise_server_exceptions=False,
            headers={"Authorization": "Bearer test-token"},
        )
        client.__enter__()

        def teardown():
            client.__exit__(None, None, None)
            gu.stop()
            for c in ctx:
                c.stop()

        return client, teardown

    @staticmethod
    def _parse_sse(raw: str) -> list[dict]:
        import json
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[len("data:"):].strip()))
                except json.JSONDecodeError:
                    pass
        return events

    def test_no_timeout_while_events_flow(self):
        """5 events spaced 0.2s apart with idle_timeout=0.5s must all pass through."""
        from langchain_core.messages import AIMessageChunk

        async def fake_astream(*args, **kwargs):
            for i in range(5):
                await asyncio.sleep(0.2)
                yield AIMessageChunk(content=f"chunk{i} "), {"langgraph_node": "agent"}

        client, teardown = self._client_with_agent(fake_astream)
        try:
            sid = client.post("/api/sessions", json={
                "mode": "react", "idle_timeout": 0.5,
            }).json()["session_id"]
            resp = client.post(f"/api/chat/{sid}", json={"message": "go"})
            events = self._parse_sse(resp.text)
            text_events = [e for e in events if e.get("type") == "text"]
            limit_events = [e for e in events if e.get("type") == "limit"]
            done_events = [e for e in events if e.get("type") == "done"]
            assert len(text_events) == 5
            assert limit_events == []
            assert len(done_events) == 1
        finally:
            teardown()

    def test_heartbeat_when_silent(self):
        """No business events while producer runs → heartbeat keeps stream alive."""
        async def fake_astream(*args, **kwargs):
            await asyncio.sleep(0.8)
            # never yields — but type-wise we must still be an async generator
            if False:
                yield None  # pragma: no cover

        client, teardown = self._client_with_agent(fake_astream)
        try:
            sid = client.post("/api/sessions", json={
                "mode": "react", "idle_timeout": 0.3,
            }).json()["session_id"]
            resp = client.post(f"/api/chat/{sid}", json={"message": "go"})
            events = self._parse_sse(resp.text)
            heartbeat_events = [e for e in events if e.get("type") == "heartbeat"]
            limit_events = [e for e in events if e.get("type") == "limit"]
            done_events = [e for e in events if e.get("type") == "done"]
            assert heartbeat_events
            assert limit_events == []
            assert len(done_events) == 1
        finally:
            teardown()
