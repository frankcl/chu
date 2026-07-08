"""Tests for the human-in-the-loop (HITL) module.

Two layers:
  - HitlChannel unit tests (pause/resume primitive).
  - Server integration: a chat stream that pauses on a `hitl` event, gets a
    POST /respond, and resumes — for both react and plan-execute modes.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch

from agent.hitl import ConsoleHitlChannel, HitlChannel, make_request_user_choice_tool


async def _wait_until(predicate, timeout=2.0):
    """Yield to the loop until predicate() is truthy (or time out)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0)


# ── HitlChannel unit tests ────────────────────────────────────────────────────

class TestHitlChannel:
    async def test_request_resolves_with_respond(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)

        task = asyncio.create_task(ch.request("pick a style", ["a", "b"]))
        await _wait_until(lambda: emitted)  # let request() emit and park on the future

        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["type"] == "hitl"
        assert ev["prompt"] == "pick a style"
        assert ev["options"] == ["a", "b"]
        assert ch.is_pending()

        assert ch.respond(ev["id"], "b") is True
        assert await task == "b"
        assert not ch.is_pending()

    async def test_stale_id_is_ignored(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)
        task = asyncio.create_task(ch.request("q", ["x", "y"]))
        await _wait_until(lambda: emitted)

        assert ch.respond("not-the-id", "x") is False
        assert ch.is_pending()

        ch.respond(emitted[0]["id"], "y")
        assert await task == "y"

    async def test_cancel_unblocks(self):
        ch = HitlChannel()
        ch.bind_emit(lambda _ev: None)
        task = asyncio.create_task(ch.request("q", ["x"]))
        await _wait_until(ch.is_pending)
        assert ch.is_pending()

        ch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ch.is_pending()

    async def test_no_emit_bound_defaults_to_first_option(self):
        """CLI / direct use with no SSE channel degrades gracefully."""
        ch = HitlChannel()
        assert await ch.request("q", ["first", "second"]) == "first"

    async def test_respond_with_no_pending_returns_false(self):
        ch = HitlChannel()
        assert ch.respond("anything", "v") is False

    async def test_preview_kind_in_event(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)
        task = asyncio.create_task(
            ch.request("pick a theme", ["a", "b"], preview="ppt-theme"))
        await _wait_until(lambda: emitted)
        assert emitted[0]["preview"] == "ppt-theme"
        ch.respond(emitted[0]["id"], "a")
        await task

    async def test_preview_defaults_to_none(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)
        task = asyncio.create_task(ch.request("q", ["a"]))
        await _wait_until(lambda: emitted)
        assert emitted[0]["preview"] is None
        ch.respond(emitted[0]["id"], "a")
        await task


# ── tool factory ──────────────────────────────────────────────────────────────

class TestRequestUserChoiceTool:
    def test_tool_name_and_args(self):
        tool = make_request_user_choice_tool(HitlChannel())
        assert tool.name == "request_user_choice"
        schema = tool.args
        assert "prompt" in schema and "options" in schema

    async def test_tool_calls_channel(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)
        tool = make_request_user_choice_tool(ch)

        task = asyncio.create_task(
            tool.ainvoke({"prompt": "pick", "options": ["m", "n"]})
        )
        await _wait_until(lambda: emitted)
        ch.respond(emitted[0]["id"], "n")
        assert await task == "n"

    def test_sync_invocation_uses_console_channel(self, monkeypatch):
        """Sync .invoke (CLI .stream path) delegates to the channel's request_sync."""
        monkeypatch.setattr("builtins.input", lambda *_a: "2")
        tool = make_request_user_choice_tool(ConsoleHitlChannel())
        result = tool.invoke({"prompt": "pick", "options": ["alpha", "beta"]})
        assert result == "beta"

    async def test_tool_forwards_preview_kind(self):
        ch = HitlChannel()
        emitted = []
        ch.bind_emit(emitted.append)
        tool = make_request_user_choice_tool(ch)
        task = asyncio.create_task(tool.ainvoke(
            {"prompt": "pick", "options": ["m", "n"], "preview_kind": "ppt-theme"}))
        await _wait_until(lambda: emitted)
        assert emitted[0]["preview"] == "ppt-theme"
        ch.respond(emitted[0]["id"], "m")
        assert await task == "m"

    def test_console_ignores_preview_kind(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_a: "1")
        tool = make_request_user_choice_tool(ConsoleHitlChannel())
        result = tool.invoke(
            {"prompt": "pick", "options": ["alpha", "beta"], "preview_kind": "ppt-theme"})
        assert result == "alpha"

    def test_sync_invocation_without_request_sync_raises(self):
        """A server HitlChannel has no request_sync — sync invocation is rejected."""
        tool = make_request_user_choice_tool(HitlChannel())
        with pytest.raises(NotImplementedError):
            tool.invoke({"prompt": "q", "options": ["a"]})


class TestConsoleHitlChannel:
    def test_accepts_index(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_a: "1")
        assert ConsoleHitlChannel().request_sync("q", ["x", "y"]) == "x"

    def test_accepts_literal_option(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_a: "y")
        assert ConsoleHitlChannel().request_sync("q", ["x", "y"]) == "y"

    def test_reprompts_on_invalid(self, monkeypatch):
        answers = iter(["bogus", "0", "2"])
        monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
        assert ConsoleHitlChannel().request_sync("q", ["x", "y"]) == "y"

    async def test_async_request_also_works(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_a: "x")
        assert await ConsoleHitlChannel().request("q", ["x", "y"]) == "x"


# ── server integration: pause → respond → resume ─────────────────────────────

def _mock_agent_driving_channel(mode: str):
    """A mock compiled graph whose astream triggers a HITL request mid-stream.

    It pulls the real HitlChannel off the (single) live session and awaits a
    human choice, then emits a follow-up event echoing the chosen value so the
    test can assert the stream resumed with the answer.
    """
    async def fake_astream(*args, **kwargs):
        import server
        channel = next(iter(server.sessions.values()))["hitl"]
        choice = await channel.request("请选择 PPT 模板风格", ["default", "tech-dark"])
        if mode == "react":
            from langchain_core.messages import AIMessageChunk
            yield AIMessageChunk(content=f"using {choice}"), {"langgraph_node": "agent"}
        else:
            yield "custom", {"phase": "summarize_token", "text": f"using {choice}"}

    agent = MagicMock()
    agent.astream = fake_astream
    return agent


def _decode_sse(raw) -> dict:
    """Parse a single `data: {...}\\n\\n` SSE chunk into its dict."""
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
    return json.loads(text[len("data:"):].strip())


async def _run_hitl_chat(mode: str, choice: str):
    """Drive a full pause/respond/resume cycle and return the parsed SSE events.

    We iterate the StreamingResponse's body generator directly (same event loop)
    rather than going through an HTTP client — httpx's ASGITransport buffers the
    whole response, so it can't deliver a mid-stream event while the generator is
    parked awaiting the human answer.
    """
    import server

    agent = _mock_agent_driving_channel(mode)
    patch_target = (
        "server.create_agent" if mode == "react" else "server.create_plan_execute_agent"
    )
    with patch(patch_target, return_value=agent):
        sid = server.create_session(server.SessionRequest(mode=mode))["session_id"]
        resp = await server.chat(sid, server.ChatRequest(message="做个PPT"))
        events = []
        async for raw in resp.body_iterator:
            ev = _decode_sse(raw)
            events.append(ev)
            if ev.get("type") == "hitl":
                # Resolve the parked request; the next iteration resumes the stream.
                assert server.sessions[sid]["hitl"].respond(ev["id"], choice) is True
            elif ev.get("type") == "done":
                break
    return events


class TestServerHitlFlow:
    async def test_react_pause_respond_resume(self):
        events = await asyncio.wait_for(_run_hitl_chat("react", "tech-dark"), timeout=10)
        types = [e.get("type") for e in events]

        hitl = [e for e in events if e["type"] == "hitl"]
        assert len(hitl) == 1
        assert hitl[0]["prompt"] == "请选择 PPT 模板风格"
        assert hitl[0]["options"] == ["default", "tech-dark"]

        texts = [e for e in events if e.get("type") == "text"]
        assert any("using tech-dark" in e.get("content", "") for e in texts)
        assert "done" in types

    async def test_plan_execute_pause_respond_resume(self):
        events = await asyncio.wait_for(_run_hitl_chat("plan-execute", "default"), timeout=10)
        types = [e.get("type") for e in events]

        assert [e for e in events if e["type"] == "hitl"]
        texts = [e for e in events if e.get("type") == "text"]
        assert any("using default" in e.get("content", "") for e in texts)
        assert "done" in types

    def test_respond_unknown_session_404(self):
        import server
        with pytest.raises(HTTPException) as exc:
            server.respond_chat("nope", server.RespondRequest(id="x", value="y"))
        assert exc.value.status_code == 404
