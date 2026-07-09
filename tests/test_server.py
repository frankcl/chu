"""Tests for server.py — FastAPI endpoints and SSE streaming."""

import json
import httpx
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ── fixtures ──────────────────────────────────────────────────────────────────

async def _empty_async_gen(*args, **kwargs):
    """Async generator that yields nothing (simulates a finished stream)."""
    return
    yield  # make it an async generator


@pytest.fixture()
def mock_react_agent():
    """A minimal mock for the compiled LangGraph react agent."""
    agent = MagicMock()
    agent.astream = _empty_async_gen
    return agent


@pytest.fixture()
def mock_plan_execute_agent():
    agent = MagicMock()
    agent.astream = _empty_async_gen
    return agent


@pytest.fixture()
def client(mock_react_agent, mock_plan_execute_agent):
    """TestClient with agent constructors patched to avoid real LLM calls.

    The hylian shield protects /api/*; its Bearer-first branch still calls
    get_user, so stub get_user (any bearer token validates) and send a default
    Authorization header on every request. Cookie/session mode is covered
    separately in TestCookieSessionLogin.
    """
    with (
        patch("server.create_agent", return_value=mock_react_agent),
        patch("server.create_plan_execute_agent", return_value=mock_plan_execute_agent),
    ):
        import server
        with patch.object(server.hylian_client, "get_user", return_value=MagicMock()):
            with TestClient(
                server.app,
                raise_server_exceptions=False,
                headers={"Authorization": "Bearer test-token"},
            ) as c:
                yield c


# ── POST /api/sessions ────────────────────────────────────────────────────────

class TestCreateSession:
    def test_returns_session_id_and_mode(self, client):
        resp = client.post("/api/sessions", json={"mode": "react"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["mode"] == "react"

    def test_default_mode_is_react(self, client):
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "react"

    def test_plan_execute_mode(self, client):
        resp = client.post("/api/sessions", json={"mode": "plan-execute"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "plan-execute"

    def test_session_ids_are_unique(self, client):
        id1 = client.post("/api/sessions", json={}).json()["session_id"]
        id2 = client.post("/api/sessions", json={}).json()["session_id"]
        assert id1 != id2


# ── 密码登录 → shield cookie 会话闭环 ─────────────────────────────────────────

class TestCookieSessionLogin:
    """验证 shield cookie/session 模式：密码登录种会话 → 仅凭 sid cookie 鉴权。"""

    def test_login_seeds_session_then_cookie_auth(
        self, mock_react_agent, mock_plan_execute_agent
    ):
        import server

        # mock hylian passwordLogin：status=True，响应头带 Token 及 TICKET/TOKEN 的
        # Set-Cookie（用 httpx.Headers 以同时支持 .get("Token") 与 .get_list("set-cookie")）。
        login_resp = MagicMock()
        login_resp.json.return_value = {"status": True}
        login_resp.headers = httpx.Headers([
            ("Token", "tok-123"),
            ("set-cookie", "TICKET=tkt; Domain=.manong.xin; Path=/; HttpOnly"),
            ("set-cookie", "TOKEN=tok; Domain=.manong.xin; Path=/"),
            ("set-cookie", "JSESSIONID=jsid; Path=/"),  # 不应被透传
        ])

        with (
            patch("server.create_agent", return_value=mock_react_agent),
            patch("server.create_plan_execute_agent", return_value=mock_plan_execute_agent),
            patch("server._hylian_request", return_value=login_resp),
            patch.object(server.hylian_client, "get_user", return_value=MagicMock()),
        ):
            # 不带 Authorization 头：纯 cookie/session 模式。
            with TestClient(server.app, raise_server_exceptions=False) as c:
                sid_name = server.hylian_client.config.session_cookie_name

                # 未登录：受保护端点被 shield 拦成 303→applyCode。
                r0 = c.post("/api/sessions", json={"mode": "react"}, follow_redirects=False)
                assert r0.status_code == 303

                # 登录：种下 shield 会话，sid cookie 已在 r0 下发并被 jar 保留。
                r1 = c.post(
                    "/api/auth/login",
                    json={"username": "u", "password": "p", "captcha": "c"},
                )
                assert r1.status_code == 200
                assert r1.json() == {"ok": True}
                assert sid_name in c.cookies
                # hylian 的 TICKET/TOKEN Set-Cookie 被透传给浏览器；JSESSIONID 不透传。
                set_cookies = r1.headers.get_list("set-cookie")
                assert any(sc.startswith("TICKET=") for sc in set_cookies)
                assert any(sc.startswith("TOKEN=") for sc in set_cookies)
                assert not any(sc.startswith("JSESSIONID=") for sc in set_cookies)

                # 之后仅凭 cookie（无 Authorization 头）即可访问受保护端点。
                r2 = c.post("/api/sessions", json={"mode": "react"})
                assert r2.status_code == 200
                assert "session_id" in r2.json()

                # 登出：清本地 shield 会话并返回 hylian logout URL，受保护端点再次被拦。
                r3 = c.post("/api/auth/logout")
                assert r3.status_code == 200
                assert "api/security/logout" in r3.json()["logout_url"]
                r4 = c.post("/api/sessions", json={"mode": "react"}, follow_redirects=False)
                assert r4.status_code == 303


# ── POST /api/title ───────────────────────────────────────────────────────────

class TestGenerateTitle:
    def test_returns_llm_summary(self, client):
        fake_resp = MagicMock()
        fake_resp.content = "黄金价格查询"
        model = MagicMock()
        model.invoke.return_value = fake_resp
        with patch("server.LLM") as LLMcls:
            LLMcls.return_value.chat_model.return_value = model
            LLMcls.extract_text = staticmethod(lambda c: c)
            resp = client.post("/api/title", json={"message": "明天黄金价格是多少"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "黄金价格查询"

    def test_falls_back_to_truncation_on_error(self, client):
        with patch("server.LLM") as LLMcls:
            LLMcls.return_value.chat_model.side_effect = RuntimeError("boom")
            resp = client.post("/api/title", json={"message": "x" * 50})
        assert resp.status_code == 200
        assert resp.json()["title"] == "x" * 24

    def test_empty_message_returns_empty(self, client):
        resp = client.post("/api/title", json={"message": "   "})
        assert resp.status_code == 200
        assert resp.json()["title"] == ""


# ── DELETE /api/sessions/{session_id} ────────────────────────────────────────

class TestDeleteSession:
    def test_delete_existing_session(self, client):
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        resp = client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_delete_nonexistent_session_still_ok(self, client):
        resp = client.delete("/api/sessions/does-not-exist")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_deleted_session_not_found_for_chat(self, client):
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        client.delete(f"/api/sessions/{session_id}")
        resp = client.post(f"/api/chat/{session_id}", json={"message": "hi"})
        assert resp.status_code == 404


# ── POST /api/chat/{session_id} ───────────────────────────────────────────────

class TestChat:
    def test_unknown_session_returns_404(self, client):
        resp = client.post("/api/chat/unknown-id", json={"message": "hello"})
        assert resp.status_code == 404

    def test_react_stream_returns_done_event(self, client):
        """A stream that yields no chunks should still emit a 'done' SSE event."""
        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]

        resp = client.post(
            f"/api/chat/{session_id}",
            json={"message": "hello"},
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events = _parse_sse(resp.text)
        types = [e.get("type") for e in events]
        assert "done" in types

    def test_react_stream_text_event(self, client, mock_react_agent):
        """When the agent emits an AIMessageChunk with text, a 'text' SSE is sent."""
        from langchain_core.messages import AIMessageChunk

        text_chunk = AIMessageChunk(content="Paris")
        metadata = {"langgraph_node": "agent"}

        async def fake_astream(*args, **kwargs):
            yield text_chunk, metadata

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "Where is Paris?"})

        events = _parse_sse(resp.text)
        text_events = [e for e in events if e.get("type") == "text"]
        assert any("Paris" in e.get("content", "") for e in text_events)

    def test_react_stream_thinking_event(self, client, mock_react_agent):
        """reasoning_content in AIMessageChunk.additional_kwargs → 'thinking' SSE."""
        from langchain_core.messages import AIMessageChunk

        thinking_chunk = AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "let me think"},
        )
        metadata = {"langgraph_node": "agent"}

        async def fake_astream(*args, **kwargs):
            yield thinking_chunk, metadata

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "?"})

        events = _parse_sse(resp.text)
        thinking_events = [e for e in events if e.get("type") == "thinking"]
        assert any("let me think" in e.get("content", "") for e in thinking_events)

    def test_react_stream_tool_event(self, client, mock_react_agent):
        """ToolMessage on the 'tools' node → 'tool' SSE."""
        from langchain_core.messages import ToolMessage

        tool_msg = ToolMessage(content="sunny, 22°C", name="get_weather", tool_call_id="1")
        metadata = {"langgraph_node": "tools"}

        async def fake_astream(*args, **kwargs):
            yield tool_msg, metadata

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "weather?"})

        events = _parse_sse(resp.text)
        tool_events = [e for e in events if e.get("type") == "tool"]
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "get_weather"
        assert "sunny" in tool_events[0]["result"]

    def test_react_stream_tool_start_event(self, client, mock_react_agent):
        """Tool start callback → 'tool_start' SSE before the ToolMessage returns."""
        from langchain_core.messages import ToolMessage

        tool_msg = ToolMessage(content="sunny", name="get_weather", tool_call_id="1")

        async def fake_astream(*args, **kwargs):
            for cb in kwargs["config"]["callbacks"]:
                if cb.__class__.__name__ == "_SseToolStartCallback":
                    cb.on_tool_start({"name": "get_weather"}, '{"location":"Paris"}')
            yield tool_msg, {"langgraph_node": "tools"}

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "weather?"})

        events = _parse_sse(resp.text)
        starts = [e for e in events if e.get("type") == "tool_start"]
        assert starts and starts[0]["name"] == "get_weather"
        assert "Paris" in starts[0]["input"]

    def test_plan_execute_stream_plan_event(self, client, mock_plan_execute_agent):
        """plan node update → 'plan' SSE event with steps list."""
        async def fake_astream(*args, **kwargs):
            yield "updates", {"plan": {"plan": ["step A", "step B"]}}

        mock_plan_execute_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "plan-execute"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "do stuff"})

        events = _parse_sse(resp.text)
        plan_events = [e for e in events if e.get("type") == "plan"]
        assert len(plan_events) == 1
        assert plan_events[0]["steps"] == ["step A", "step B"]

    def test_plan_execute_stream_step_and_text_events(self, client, mock_plan_execute_agent):
        """execute updates → 'step'; summarize_token custom event → 'text'."""
        async def fake_astream(*args, **kwargs):
            yield "updates", {"execute": {"past_steps": [("do X", "done X")]}}
            yield "custom", {"phase": "summarize_token", "text": "All done."}

        mock_plan_execute_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "plan-execute"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "task"})

        events = _parse_sse(resp.text)
        step_events = [e for e in events if e.get("type") == "step"]
        text_events = [e for e in events if e.get("type") == "text"]

        assert len(step_events) == 1
        assert step_events[0]["step"] == "do X"
        assert len(text_events) == 1
        assert text_events[0]["content"] == "All done."

    def test_plan_execute_streams_phase_and_step_progress(self, client, mock_plan_execute_agent):
        """Verify all the new streaming events: phase, step_start, step_token, step_tool."""
        async def fake_astream(*args, **kwargs):
            yield "custom", {"phase": "planning_start"}
            yield "updates", {"plan": {"plan": ["s1"]}}
            yield "custom", {"phase": "execute_start", "step_num": 1, "total": 1, "task": "s1"}
            yield "custom", {"phase": "execute_thinking", "step_num": 1, "text": "let me reason"}
            yield "custom", {"phase": "execute_token", "step_num": 1, "text": "thinking…"}
            yield "custom", {"phase": "execute_tool_start", "step_num": 1, "name": "get_weather", "input": "{}"}
            yield "custom", {"phase": "execute_tool", "step_num": 1, "name": "get_weather", "result": "sunny"}
            yield "updates", {"execute": {"past_steps": [("s1", "done")]}}
            yield "custom", {"phase": "summarize_start"}
            yield "custom", {"phase": "summarize_token", "text": "All set."}

        mock_plan_execute_agent.astream = fake_astream

        sid = client.post("/api/sessions", json={"mode": "plan-execute"}).json()["session_id"]
        resp = client.post(f"/api/chat/{sid}", json={"message": "go"})
        events = _parse_sse(resp.text)

        phases = [e for e in events if e.get("type") == "phase"]
        assert [p["phase"] for p in phases] == ["planning", "summarizing"]

        starts = [e for e in events if e.get("type") == "step_start"]
        assert len(starts) == 1
        assert starts[0]["step_num"] == 1 and starts[0]["task"] == "s1"

        tokens = [e for e in events if e.get("type") == "step_token"]
        assert tokens and tokens[0]["text"] == "thinking…"

        thinks = [e for e in events if e.get("type") == "step_thinking"]
        assert thinks and thinks[0]["text"] == "let me reason"
        assert thinks[0]["step_num"] == 1

        tools = [e for e in events if e.get("type") == "step_tool"]
        assert tools and tools[0]["name"] == "get_weather"

        tool_starts = [e for e in events if e.get("type") == "step_tool_start"]
        assert tool_starts and tool_starts[0]["name"] == "get_weather"

        texts = [e for e in events if e.get("type") == "text"]
        assert texts and texts[0]["content"] == "All set."

    def test_stream_error_event_on_exception(self, client, mock_react_agent):
        """Exceptions inside the stream generator must yield an 'error' SSE."""
        async def exploding_astream(*args, **kwargs):
            raise RuntimeError("LLM exploded")
            yield  # make it a generator

        mock_react_agent.astream = exploding_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "boom"})

        events = _parse_sse(resp.text)
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "LLM exploded" in error_events[0]["message"]

    def test_content_moderation_becomes_limit_event(self, client, mock_react_agent):
        """Provider content-moderation rejections → clean 'limit' (content_filter), not raw 'error'."""
        class _ModerationError(Exception):
            code = "data_inspection_failed"

        async def blocked_astream(*args, **kwargs):
            raise _ModerationError(
                "Output data may contain inappropriate content. For details, see: "
                "https://help.aliyun.com/zh/model-studio/error-code#inappropriate-content"
            )
            yield

        mock_react_agent.astream = blocked_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "明天A股走势如何"})

        events = _parse_sse(resp.text)
        types = [e.get("type") for e in events]
        assert "error" not in types
        limit_events = [e for e in events if e.get("type") == "limit"]
        assert len(limit_events) == 1
        assert limit_events[0]["reason"] == "content_filter"
        assert "done" in types

    def test_plan_execute_stream_error_event(self, client, mock_plan_execute_agent):
        """Exceptions inside plan-execute stream → 'error' SSE event."""
        async def exploding_astream(*args, **kwargs):
            raise ValueError("plan failed")
            yield

        mock_plan_execute_agent.astream = exploding_astream

        session_id = client.post("/api/sessions", json={"mode": "plan-execute"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "task"})

        events = _parse_sse(resp.text)
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "plan failed" in error_events[0]["message"]

    def test_react_stream_anthropic_thinking_blocks(self, client, mock_react_agent):
        """Anthropic 'thinking' content blocks in AIMessageChunk → 'thinking' SSE events."""
        from langchain_core.messages import AIMessageChunk

        chunk = AIMessageChunk(
            content=[{"type": "thinking", "thinking": "anthropic thinking text"}],
        )
        metadata = {"langgraph_node": "agent"}

        async def fake_astream(*args, **kwargs):
            yield chunk, metadata

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={"mode": "react"}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "?"})

        events = _parse_sse(resp.text)
        thinking_events = [e for e in events if e.get("type") == "thinking"]
        assert any("anthropic thinking text" in e.get("content", "") for e in thinking_events)

    def test_sse_content_type_header(self, client):
        """The /api/chat endpoint must respond with text/event-stream."""
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "hi"})
        assert "text/event-stream" in resp.headers["content-type"]

    def test_tool_result_truncated_to_800_chars(self, client, mock_react_agent):
        """Tool results longer than 800 chars must be truncated in the SSE event."""
        from langchain_core.messages import ToolMessage

        tool_msg = ToolMessage(content="x" * 1000, name="big_tool", tool_call_id="1")
        metadata = {"langgraph_node": "tools"}

        async def fake_astream(*args, **kwargs):
            yield tool_msg, metadata

        mock_react_agent.astream = fake_astream

        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        resp = client.post(f"/api/chat/{session_id}", json={"message": "?"})

        events = _parse_sse(resp.text)
        tool_events = [e for e in events if e.get("type") == "tool"]
        assert len(tool_events) == 1
        assert len(tool_events[0]["result"]) <= 800


# ── GET /api/files/{filename} ─────────────────────────────────────────────────

class TestDownloadFile:
    def test_serves_existing_file(self, client, tmp_path):
        deck = tmp_path / "deck.pptx"
        deck.write_bytes(b"PK\x03\x04 fake pptx")
        with patch("server.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/deck.pptx")
        assert resp.status_code == 200
        assert resp.content == b"PK\x03\x04 fake pptx"

    def test_missing_file_returns_404(self, client, tmp_path):
        with patch("server.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/nope.pptx")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, tmp_path):
        (tmp_path / "deck.pptx").write_bytes(b"x")
        with patch("server.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/..%2f..%2fserver.py")
        assert resp.status_code == 404


# ── GET /api/ppt/themes ───────────────────────────────────────────────────────

class TestPptThemes:
    def test_returns_four_themes_with_colors(self, client):
        resp = client.get("/api/ppt/themes")
        assert resp.status_code == 200
        themes = resp.json()["themes"]
        assert [t["name"] for t in themes] == [
            "default", "business-blue", "tech-dark", "minimal"]
        tech = next(t for t in themes if t["name"] == "tech-dark")
        assert tech["colors"]["band"] == "2E6CB5"
        assert tech["label"]
        assert tech["sample"]["body"]


# ── SSE parsing helper ────────────────────────────────────────────────────────

def _parse_sse(raw: str) -> list[dict]:
    """Parse raw SSE text into a list of data dicts."""
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events
