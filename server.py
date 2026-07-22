import asyncio
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import oss2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from hylian_client.sdk import context
from hylian_client.sdk.fastapi import enable_hylian_shield
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from starlette.concurrency import run_in_threadpool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from agent import (
    LLM,
    BudgetExceededError,
    BudgetTracker,
    HarnessConfig,
    HitlChannel,
    create_agent,
    create_plan_execute_agent,
    iter_chunk_outputs,
)
from agent.source_meta import extract_source_favicons
import storage as db
from logger import get_logger

load_dotenv()
logger = get_logger("server")

# 对话历史持久化（MySQL）。未配置则禁用，历史相关 helper 优雅降级、不阻断对话。
db.init_db()

app = FastAPI()

# 接入 hylian SSO：shield（cookie/session 模式）中间件 + CORS（SDK 统一管理中间件顺序）。
# 配置从 HYLIAN_* 环境变量读取；HYLIAN_EXCLUDE_PATTERNS 命中的路径放行。
# token/user 缓存在服务端会话，浏览器只持 httpOnly 的 sid cookie（HYLIAN_SESSION）。
# 返回的 client 复用于下面的密码登录路由；session_manager 用于种/失效会话。
hylian_client = enable_hylian_shield(app, cors_origins=["http://localhost:5173"])
session_manager = app.state.hylian_session_manager
# SDK 内部的 httpx 客户端默认会走本机代理（trust_env），而代理用公网 DNS 会把
# hylian.manong.xin 解析到远程实例、绕过 /etc/hosts 的本地 hylian，导致 verifyApp
# 401。换成 trust_env=False 的直连客户端，让 getUser/refreshToken 打到本地 hylian。
# SessionManager 复用同一 client 对象，这里的覆盖对它的 get_user/refresh 一并生效。
hylian_client._client = httpx.Client(
    verify=hylian_client.config.verify_tls,
    timeout=hylian_client.config.timeout,
    trust_env=False,
)

# 阿里云 OSS：用于对用户头像做私有读加签（sign_url 生成带签名的临时访问 URL）。
# 配置从环境变量读取；缺任一项则视为未配置——加签退化为原样返回，不阻断登录。
_OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "")
_OSS_BUCKET_NAME = os.getenv("OSS_BUCKET", "")
_OSS_URL_EXPIRE_SECONDS = int(os.getenv("OSS_URL_EXPIRE_SECONDS", "3600"))


def _make_oss_bucket() -> oss2.Bucket | None:
    key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    if not (_OSS_ENDPOINT and _OSS_BUCKET_NAME and key_id and key_secret):
        logger.warning("OSS 未配置完整，头像加签将退化为原样返回")
        return None
    auth = oss2.Auth(key_id, key_secret)
    return oss2.Bucket(auth, _OSS_ENDPOINT, _OSS_BUCKET_NAME)


_oss_bucket = _make_oss_bucket()


def sign_avatar(avatar: str | None) -> str | None:
    """对头像做 OSS 加签，返回带签名的临时访问 URL。

    avatar 可能是纯 object key（如 avatars/1.png）或完整 URL（如
    https://bucket.oss-cn-xxx.aliyuncs.com/avatars/1.png）——完整 URL 取其 path
    作为 key。未配置 OSS 或加签失败时原样返回，保证登录流程不被头像问题打断。
    """
    if not avatar or _oss_bucket is None:
        return avatar
    key = urlparse(avatar).path.lstrip("/") if avatar.startswith(("http://", "https://")) else avatar
    if not key:
        return avatar
    try:
        return _oss_bucket.sign_url("GET", key, _OSS_URL_EXPIRE_SECONDS, slash_safe=True)
    except Exception:  # noqa: BLE001 — 加签失败不应阻断登录，退回原值
        logger.exception("头像 OSS 加签失败，返回原始 avatar")
        return avatar


# 后端代理 hylian 的密码登录：passwordLogin 成功后把 token 放在响应头 `Token`
# 返回（见 hylian-server LoginManagement.setUserLogin）。验证码按 hylian 的
# HttpSession(JSESSIONID) 校验，申请与提交必须同一 session——用下面这个自有
# cookie 承载 JSESSIONID，把两步关联起来（仅在 /api/auth 路径回传）。
_SESSION_COOKIE = "HYLIAN_JSESSIONID"


def _hylian_request(method: str, path: str, **kwargs) -> httpx.Response:
    """每次用全新客户端请求 hylian：空 cookie jar，只发送本次显式传入的 cookie。

    绝不能复用带 jar 的客户端——一次成功登录会把 hylian 下发的 TICKET/TOKEN
    留在 jar 里，之后的 passwordLogin 会被 hylian 当成「已登录」而走提前返回分支
    （不再下发 `Token` 响应头），导致登录拿不到 token。
    """
    cfg = hylian_client.config
    # trust_env=False：不读 HTTP(S)_PROXY，直连——否则会走本机代理，代理用公网 DNS
    # 把 hylian.manong.xin 解析到远程实例，绕过 /etc/hosts 的本地 hylian。
    with httpx.Client(verify=cfg.verify_tls, timeout=cfg.timeout, trust_env=False) as client:
        return client.request(method, f"{cfg.server_url}{path}", **kwargs)


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha: str


@app.get("/api/auth/captcha")
def auth_captcha(response: Response) -> dict:
    """申请验证码：返回验证码明文（前端自行绘制成图片），并把 hylian 下发的
    JSESSIONID 转存到本域 cookie，供随后的登录复用同一 session。"""
    r = _hylian_request("GET", "api/captcha/apply")
    envelope = r.json()
    if not envelope.get("status"):
        raise HTTPException(status_code=502, detail="验证码申请失败")
    jsid = r.cookies.get("JSESSIONID")
    if jsid:
        response.set_cookie(
            _SESSION_COOKIE, jsid, path="/api/auth",
            httponly=True, secure=True, samesite="lax",
        )
    return {"captcha": envelope["data"]}


@app.post("/api/auth/login")
def auth_login(body: LoginRequest, request: Request) -> dict:
    """密码登录：转发到 hylian passwordLogin（带上验证码申请时的 JSESSIONID），
    成功后从 `Token` 响应头取出 token，种入当前 shield 会话（服务端缓存 token+user）。

    此后浏览器仅凭中间件下发的 httpOnly sid cookie（HYLIAN_SESSION）鉴权，
    前端不再持有 token。种会话复用 shield ?code= 分支内部的三步。
    """
    jsid = request.cookies.get(_SESSION_COOKIE)
    r = _hylian_request(
        "POST", "api/security/passwordLogin",
        json={"username": body.username, "password": body.password, "captcha": body.captcha},
        cookies={"JSESSIONID": jsid} if jsid else None,
    )
    envelope = r.json()
    if not envelope.get("status"):
        raise HTTPException(status_code=401, detail=envelope.get("message") or "登录失败")
    token = r.headers.get("Token")
    if not token:
        raise HTTPException(status_code=502, detail="hylian 未返回 token")
    # 把 token 写进本次请求的 shield 会话；sid cookie 由中间件自动下发。
    session = request.state.hylian_session
    session_manager.mark_token_session(session)
    session.set_token(token)
    session.user = hylian_client.get_user(token)  # 预热 user，省掉首个受保护请求的往返
    session_manager.touch(session)
    # 透传 hylian 的 TICKET/TOKEN Set-Cookie 给浏览器（Domain=.manong.xin 父域共享），
    # 使浏览器在 SSO 域下持有它们——登出时才能带 TICKET 直接命中 hylian logout 清理。
    # passwordLogin 由后端代理调用，这些 Set-Cookie 本落在一次性 httpx client 上会被丢弃。
    resp = JSONResponse({"ok": True})
    for raw in r.headers.get_list("set-cookie"):
        if raw.startswith(("TICKET=", "TOKEN=")):  # 只透传 SSO cookie，跳过 hylian JSESSIONID
            resp.raw_headers.append((b"set-cookie", raw.encode("latin-1")))
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> Response:
    """登出：清本地 shield 会话与本域 cookie，并返回 hylian logout URL。

    hylian 服务端的 ticket/token + 其域下 cookie 需由**浏览器**带 .manong.xin 的 TICKET
    cookie 直接命中 hylian logout 才能清理（后端代理拿不到浏览器的 TICKET cookie），
    故这里只返回 URL 交给前端去打（见 frontend/src/api/auth.js 的 logout）。
    """
    session_manager.invalidate(request.state.hylian_session.sid)
    resp = JSONResponse({"logout_url": hylian_client.logout_redirect_url()})
    resp.delete_cookie(hylian_client.config.session_cookie_name, path="/")  # HYLIAN_SESSION
    resp.delete_cookie(_SESSION_COOKIE, path="/api/auth")                   # HYLIAN_JSESSIONID
    return resp


@app.get("/api/auth/me")
def auth_me() -> dict:
    """返回当前登录用户的展示信息（头像 / 用户名）。

    该路由受 shield 保护（不在 exclude 内），用户由中间件校验会话后注入 context；
    未登录时中间件已 303→applyCode / 401，不会走到这里。只回传展示所需字段，
    避免把 password 等敏感字段透给前端。
    """
    user = context.get_user()
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return {
        "username": user.username,
        "name": user.name or user.username,  # 展示名：优先 name，回退 username
        "avatar": sign_avatar(user.avatar),  # OSS 加签后的临时 URL（未配置则原样，可能为 None）
        # 以下为非敏感展示字段（值可能为 None，前端按需渲染）；
        # 继续排除 password / wx_openid / disabled / register_mode / id / tenant_id。
        "email": user.email,
        "phone": user.phone,
        "company": user.company,
        "position": user.position,
        "industry": user.industry,
        "location": " ".join(p for p in (user.province, user.city, user.district) if p) or None,
        "tenant": user.tenant.name if user.tenant else None,
        "super_admin": user.super_admin,
        "register_time": user.create_time,  # ms epoch，前端格式化为日期
    }

# session_id -> {"agent", "mode", "thread_id", "harness", "active_task", "hitl", "user_id"}
# session_id == thread_id == 持久化的 chat_session.id（历史与记忆共享同一 id）。
sessions: dict[str, dict] = {}


def _current_user_id() -> str | None:
    """当前请求的登录用户 id（受保护路由由 hylian guard 注入到 context）。"""
    user = context.get_user()
    return user.id if user is not None else None

# Shared directory where generated artifacts (e.g. ppt decks) are written and
# served from. Skills like ppt/build.py save into this same folder.
GENERATED_DIR = (Path(__file__).resolve().parent / "generated")


@app.get("/api/files/{filename}")
def download_file(filename: str):
    """Serve a generated artifact (e.g. a .pptx deck) for download.

    Only bare filenames inside GENERATED_DIR are served — anything resolving
    outside the folder (path traversal) or missing returns 404.
    """
    base = GENERATED_DIR.resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


# Lazy path-load of the ppt theme palettes (skills/ppt/themes.py — a pure-data
# module with no heavy deps) so the frontend's preview cards read the same colors
# build.py renders with, without the server hard-importing the skills package.
_PPT_THEMES_MOD = None


def _ppt_themes_module():
    global _PPT_THEMES_MOD
    if _PPT_THEMES_MOD is None:
        path = Path(__file__).resolve().parent / "skills" / "ppt" / "themes.py"
        spec = importlib.util.spec_from_file_location("ppt_themes", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PPT_THEMES_MOD = mod
    return _PPT_THEMES_MOD


@app.get("/api/ppt/themes")
def ppt_themes():
    """Per-theme preview data (colors + sample text) for the template picker UI."""
    return {"themes": _ppt_themes_module().theme_previews()}


class SessionRequest(BaseModel):
    mode: str = "react"
    conversation_id: str | None = None  # 传入则「继续」该历史对话（复用其 id 作 thread_id）
    recursion_limit: int | None = None
    idle_timeout: float | None = None
    per_tool_timeout: float | None = None
    max_tool_calls: int | None = None
    max_tool_calls_per_task: int | None = None
    max_skill_script_calls_per_task: int | None = None
    max_parallel_tasks: int | None = None
    max_tokens: int | None = None
    llm_max_retries: int | None = None
    tool_allowlist: list[str] | None = None
    tool_denylist: list[str] | None = None


class ChatRequest(BaseModel):
    message: str


class RespondRequest(BaseModel):
    id: str
    value: str


class TitleRequest(BaseModel):
    message: str


_TITLE_SYSTEM = (
    "你是会话标题生成器。根据用户的消息，用简洁的中文概括其主题，生成一个不超过 12 个字"
    "的短标题。只输出标题本身，不要引号、标点符号、序号或任何多余解释。"
)


@app.post("/api/title")
def generate_title(body: TitleRequest):
    """Summarize the first user message into a short sidebar title via the LLM.

    Best-effort: on any failure (provider error, content moderation, empty
    output) we fall back to a truncated copy of the message so the caller always
    gets a usable title.
    """
    text = body.message.strip()
    fallback = text[:24]
    if not text:
        return {"title": fallback}
    try:
        resp = LLM().chat_model(thinking=False).invoke([
            SystemMessage(content=_TITLE_SYSTEM),
            HumanMessage(content=text[:500]),
        ])
        title = LLM.extract_text(resp.content).strip().strip("\"'《》 ")
        title = title.splitlines()[0][:20] if title else ""
        return {"title": title or fallback}
    except Exception as exc:
        logger.warning("title generation failed: %s", exc)
        return {"title": fallback}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_content_moderation_error(exc: Exception) -> bool:
    """Detect provider-side content-moderation rejections.

    Aliyun DashScope (Qwen) enforces output content inspection on the platform
    side — for sensitive topics (e.g. stock/financial advice in China) it aborts
    the stream with an APIError carrying code `data_inspection_failed` / a message
    about "inappropriate content". This is a policy block, not a transient fault,
    so we surface it as a clean `limit` event instead of a raw error.
    """
    code = str(getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    return (
        "data_inspection_failed" in code
        or "data_inspection_failed" in msg
        or "inappropriate content" in msg
    )


class _SseToolStartCallback(BaseCallbackHandler):
    """Emit a generic SSE tool_start event for ReAct mode tool calls."""

    def __init__(self, emit):
        self.emit = emit

    def on_tool_start(self, serialized, input_str, **kwargs):  # type: ignore[override]
        name = ""
        if isinstance(serialized, dict):
            name = serialized.get("name") or serialized.get("id") or ""
        self.emit({
            "type": "tool_start",
            "name": str(name or "tool"),
            "input": str(input_str or "")[:500],
        })


def _harness_from_request(body: SessionRequest) -> HarnessConfig:
    overrides = body.model_dump(exclude={"mode"}, exclude_none=True)
    return HarnessConfig.from_env().merge(overrides)


def _build_session_record(session_id: str, mode: str, harness: HarnessConfig, user_id: str | None) -> dict:
    """构建运行时 session（编译好的 agent + 内存 checkpointer + hitl）。

    session_id 同时用作 thread_id —— 与持久化的 chat_session.id 一致，历史与记忆共享 id。
    """
    checkpointer = MemorySaver()
    hitl = HitlChannel()
    if mode == "plan-execute":
        agent = create_plan_execute_agent(
            checkpointer=checkpointer, harness=harness, hitl_channel=hitl,
        )
    else:
        agent = create_agent(checkpointer=checkpointer, harness=harness, hitl_channel=hitl)
    return {
        "agent": agent,
        "mode": mode,
        "thread_id": session_id,
        "harness": harness,
        "active_task": None,
        "hitl": hitl,
        "user_id": user_id,
    }


def _rebuild_memory(record: dict, session_id: str, user_id: str) -> int:
    """从历史「重建记忆」：只取 user / assistant-text 消息注入 checkpointer。

    这是「对话历史」与「对话记忆」的分界 —— thinking/tool/plan/step 只属历史、
    不进入喂给 LLM 的上下文。返回注入的消息条数。
    """
    history = db.get_messages(session_id, user_id) or []
    prior = [
        ("human" if m["role"] == "user" else "ai", m["content"])
        for m in history
        if m["type"] == "text" and m.get("content")
    ]
    if prior:
        record["agent"].update_state(
            {"configurable": {"thread_id": session_id}}, {"messages": prior}
        )
    return len(prior)


@app.post("/api/sessions")
def create_session(body: SessionRequest):
    user_id = _current_user_id()
    harness = _harness_from_request(body)

    if body.conversation_id:
        # 继续历史对话：复用其 id（= thread_id），从历史重建记忆。
        session_id = body.conversation_id
        if db.get_owner(session_id) != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        existing = sessions.get(session_id)
        if existing is None or existing.get("mode") != body.mode:
            record = _build_session_record(session_id, body.mode, harness, user_id)
            n = _rebuild_memory(record, session_id, user_id)
            sessions[session_id] = record
            logger.info("session continued id=%s mode=%s rebuilt_msgs=%d replaced=%s",
                        session_id, body.mode, n, existing is not None)
    else:
        # 新对话：生成一个 id 兼作 thread_id；chat_session 首消息落库时懒创建。
        session_id = str(uuid.uuid4())
        sessions[session_id] = _build_session_record(session_id, body.mode, harness, user_id)
        logger.info("session created id=%s mode=%s harness=%s",
                    session_id, body.mode, harness)

    return {"session_id": session_id, "mode": body.mode}


def _teardown_session(session_id: str) -> bool:
    """清理运行时 session：取消 hitl/active_task，并显式清空该 thread 的对话记忆
    （MemorySaver checkpointer）。返回是否确有该运行时 session。"""
    s = sessions.pop(session_id, None)
    if not s:
        return False
    if s.get("hitl"):
        s["hitl"].cancel()
    if s.get("active_task"):
        s["active_task"].cancel()
    # 显式删除对话记忆：不能只靠丢引用等 GC（active_task 未结束时仍被引用）。
    checkpointer = getattr(s.get("agent"), "checkpointer", None)
    if checkpointer is not None:
        try:
            checkpointer.delete_thread(session_id)
        except Exception:  # noqa: BLE001 — 记忆清理失败不应阻断删除
            logger.exception("delete_thread 失败 session=%s", session_id)
    return True


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    _teardown_session(session_id)
    logger.info("session deleted id=%s", session_id)
    return {"ok": True}


class TitleUpdate(BaseModel):
    title: str


class TopUpdate(BaseModel):
    top: bool


@app.get("/api/conversations")
def list_conversations(start_time: int | None = None, end_time: int | None = None):
    """当前用户的对话历史列表（按 update_time 倒序）。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    return {"conversations": db.list_conversations(user_id, start_time=start_time, end_time=end_time)}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    """某场对话的全部消息（全量保真），仅 owner 可读。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    messages = db.get_messages(conversation_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": conversation_id, "messages": messages}


@app.delete("/api/conversations")
def clear_conversations():
    """清除当前用户的全部对话历史：运行时 session + 对话记忆(MemorySaver) + DB 行。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    # 先清该用户所有运行时 session（含未落库的新对话，一并清 MemorySaver）。
    for sid, record in list(sessions.items()):
        if record.get("user_id") == user_id:
            _teardown_session(sid)
    deleted = db.delete_user_history(user_id)  # 删 DB 历史
    logger.info("conversations cleared user=%s db_deleted=%d", user_id, len(deleted))
    return {"ok": True, "deleted": len(deleted)}


@app.post("/api/conversations/{conversation_id}/title")
def set_conversation_title(conversation_id: str, body: TitleUpdate):
    """持久化前端 LLM 概括出的会话标题（覆盖首消息截断的临时标题）。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    if db.get_owner(conversation_id) != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.update_title(conversation_id, body.title)
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/top")
def set_conversation_top(conversation_id: str, body: TopUpdate):
    """置顶/取消置顶对话，仅 owner 可操作。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    if not db.set_top(conversation_id, user_id, body.top):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    """删除对话历史：清运行时 session + 对话记忆(MemorySaver) + DB 行，仅 owner 可删。

    未持久化的新对话（只有运行时、无 DB 行）也能被清干净——不再因 DB 无行而提前 404
    跳过运行时清理。
    """
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")

    record = sessions.get(conversation_id)
    owns_runtime = record is not None and record.get("user_id") == user_id
    owns_db = db.get_owner(conversation_id) == user_id
    if not owns_runtime and not owns_db:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if owns_runtime:
        _teardown_session(conversation_id)  # 清运行时 session + 对话记忆
    if owns_db:
        db.delete_conversation(conversation_id, user_id)  # 删 DB 历史
    logger.info("conversation deleted id=%s runtime=%s db=%s",
                conversation_id, owns_runtime, owns_db)
    return {"ok": True}


@app.get("/api/stats/tokens")
def token_stats():
    """当前登录用户的 token 消耗统计：总量 + 按日期趋势。"""
    user_id = _current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    return db.user_token_stats(user_id)


@app.post("/api/chat/{session_id}/cancel")
def cancel_chat(session_id: str):
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.get("hitl"):
        s["hitl"].cancel()
    task = s.get("active_task")
    if task and not task.done():
        task.cancel()
        logger.info("cancel requested session=%s", session_id)
        return {"ok": True, "cancelled": True}
    return {"ok": True, "cancelled": False}


@app.post("/api/chat/{session_id}/respond")
def respond_chat(session_id: str, body: RespondRequest):
    """Deliver a human's answer to a pending HITL request; the open SSE stream resumes."""
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    hitl: HitlChannel = s["hitl"]
    ok = hitl.respond(body.id, body.value)
    logger.info("hitl respond session=%s id=%s ok=%s", session_id, body.id, ok)
    return {"ok": ok}


class _TurnRecorder:
    """累积一轮对话的 AI 全量产出，落库时转成 chat_message 行（全量保真）。

    观察流中已入队的 typed item（同 SSE 下发的内容），按类型聚合：文本、思考、
    工具调用、plan、plan-execute 各步骤。
    """

    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.tools: list[dict] = []          # {name, result}
        self.plan: list = []
        self.steps: dict[int, dict] = {}     # step_num -> {task, text, thinking, tools}

    def _step(self, num: int) -> dict:
        return self.steps.setdefault(num, {"task": "", "text": "", "thinking": "", "tools": []})

    def observe(self, item: dict) -> None:
        t = item.get("type")
        if t == "text":
            self.text += item.get("content", "")
        elif t == "thinking":
            self.thinking += item.get("content", "")
        elif t == "tool":
            self.tools.append({
                "name": item.get("name"),
                "result": item.get("result", ""),
                "source_favicons": item.get("source_favicons") or [],
            })
        elif t == "plan":
            self.plan = item.get("steps", [])
        elif t == "step_start":
            self._step(item["step_num"])["task"] = item.get("task", "")
        elif t == "step_token":
            self._step(item["step_num"])["text"] += item.get("text", "")
        elif t == "step_thinking":
            self._step(item["step_num"])["thinking"] += item.get("text", "")
        elif t == "step_tool":
            self._step(item["step_num"])["tools"].append(
                {
                    "name": item.get("name"),
                    "result": item.get("result", ""),
                    "source_favicons": item.get("source_favicons") or [],
                }
            )

    def rows(self) -> list[dict]:
        """转成待落库的消息行（每类一行；type 区分）。"""
        rows: list[dict] = []
        if self.thinking:
            rows.append({"role": "assistant", "type": "thinking", "content": self.thinking})
        for tl in self.tools:
            rows.append({"role": "assistant", "type": "tool",
                         "content": tl["result"], "extra": {
                             "name": tl["name"],
                             "source_favicons": tl.get("source_favicons") or [],
                         }})
        if self.plan:
            rows.append({"role": "assistant", "type": "plan", "extra": {"steps": self.plan}})
        for num in sorted(self.steps):
            st = self.steps[num]
            rows.append({"role": "assistant", "type": "step", "content": st["text"],
                         "extra": {"step_num": num, "task": st["task"],
                                   "thinking": st["thinking"], "tools": st["tools"]}})
        if self.text:
            rows.append({"role": "assistant", "type": "text", "content": self.text})
        return rows


@app.post("/api/chat/{session_id}")
async def chat(session_id: str, body: ChatRequest):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    agent = session["agent"]
    mode = session["mode"]
    harness: HarnessConfig = session["harness"]
    tracker = BudgetTracker(harness)
    run_config = {
        # skill_call_counts：本轮 run_skill_script 的每 (skill,script) 计数容器，
        # 供 skills.py 的硬闸限制反复触发（如 web-research 搜索）。每轮新建即重置。
        "configurable": {
            "thread_id": session["thread_id"],
            "skill_call_counts": {},
            "max_skill_script_calls_per_task": harness.max_skill_script_calls_per_task,
        },
        "recursion_limit": harness.recursion_limit,
        "callbacks": [tracker],
    }
    short_msg = body.message[:60] + ("…" if len(body.message) > 60 else "")
    logger.info("chat request session=%s mode=%s message=%r", session_id, mode, short_msg)

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    # Let the HITL tool emit its question onto this request's SSE queue; the
    # request_user_choice tool then awaits the user's answer (POST /respond).
    hitl: HitlChannel = session["hitl"]
    hitl.bind_emit(queue.put_nowait)
    if mode == "react":
        run_config["callbacks"].append(_SseToolStartCallback(queue.put_nowait))

    # 对话历史落库：session_id == chat_session.id。用户消息先落库、AI 产出累积后
    # 在流终止点落库（部分完成也存）。未配置 MySQL / 未登录则跳过。
    user_id = session.get("user_id")
    persist = db.enabled() and user_id is not None
    recorder = _TurnRecorder()

    async def produce_react():
        try:
            async for chunk, metadata in agent.astream(
                {"messages": [("human", body.message)]},
                config=run_config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node")
                if node == "agent" and isinstance(chunk, AIMessageChunk):
                    for kind, text in iter_chunk_outputs(chunk):
                        if kind == "thinking":
                            await queue.put({"type": "thinking", "content": text})
                        else:
                            await queue.put({"type": "text", "content": text})
                elif node == "tools" and isinstance(chunk, ToolMessage):
                    result = (
                        chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                    )
                    await queue.put({
                        "type": "tool",
                        "name": chunk.name,
                        "result": result[:800],
                        "source_favicons": extract_source_favicons(result),
                    })
        finally:
            await queue.put(SENTINEL)

    async def produce_plan_execute():
        try:
            async for mode, data in agent.astream(
                {
                    "input": body.message,
                    "plan": [],
                    "plan_total": 0,
                    "tasks": [],
                    "task_results": {},
                    "task_errors": {},
                    "past_steps": [],
                    "response": None,
                },
                config=run_config,
                stream_mode=["updates", "custom"],
            ):
                if mode == "custom":
                    phase = data.get("phase")
                    if phase == "planning_start":
                        await queue.put({"type": "phase", "phase": "planning"})
                    elif phase == "execute_start":
                        await queue.put({
                            "type": "step_start",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "total": data["total"],
                            "task": data["task"],
                        })
                    elif phase == "execute_token":
                        await queue.put({
                            "type": "step_token",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "text": data["text"],
                        })
                    elif phase == "execute_thinking":
                        await queue.put({
                            "type": "step_thinking",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "text": data["text"],
                        })
                    elif phase == "execute_tool":
                        await queue.put({
                            "type": "step_tool",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "tool_call_id": data.get("tool_call_id"),
                            "name": data["name"],
                            "result": data["result"],
                            "source_favicons": data.get("source_favicons") or [],
                        })
                    elif phase == "execute_tool_start":
                        await queue.put({
                            "type": "step_tool_start",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "tool_call_id": data.get("tool_call_id"),
                            "name": data["name"],
                            "input": data.get("input", ""),
                        })
                    elif phase == "execute_done":
                        await queue.put({
                            "type": "step_done",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                        })
                    elif phase == "execute_failed":
                        await queue.put({
                            "type": "step_failed",
                            "task_id": data.get("task_id"),
                            "step_num": data["step_num"],
                            "message": data["error"],
                        })
                    elif phase == "summarize_start":
                        await queue.put({"type": "phase", "phase": "summarizing"})
                    elif phase == "summarize_token":
                        await queue.put({"type": "text", "content": data["text"]})
                    elif phase == "summarize_thinking":
                        await queue.put({"type": "thinking", "content": data["text"]})
                else:  # mode == "updates"
                    for node, payload in data.items():
                        if node == "plan":
                            steps = payload.get("plan", [])
                            if steps:
                                await queue.put({"type": "plan", "steps": steps})
                        elif node == "execute":
                            for step, result in payload.get("past_steps", []):
                                await queue.put({"type": "step", "step": step, "result": result})
                        # summarize node intentionally not forwarded: its content
                        # was already streamed via summarize_token above.
        finally:
            await queue.put(SENTINEL)

    producer_coro = produce_react() if mode == "react" else produce_plan_execute()

    async def stream():
        t0 = time.monotonic()
        # 用户消息先落库（首条消息时懒创建 chat_session，标题取首消息截断）。
        if persist:
            await run_in_threadpool(
                db.create_conversation, session_id, user_id, body.message[:24]
            )
            await run_in_threadpool(
                db.append_messages, session_id, user_id,
                [{"role": "user", "type": "text", "content": body.message}],
            )
        producer = asyncio.create_task(producer_coro)
        session["active_task"] = producer
        # idle timeout: each wait_for caps the gap between two events, not
        # the overall stream duration. If the producer is still running, a quiet
        # period means a long LLM/tool call is in flight, so emit a heartbeat
        # instead of killing the request.
        idle = harness.idle_timeout if harness.idle_timeout else None
        heartbeat_interval = min(15.0, idle) if idle is not None else None
        try:
            while True:
                # While a HITL question is outstanding, the human is deciding —
                # that is not an idle/stuck stream, so don't apply the heartbeat timeout.
                timeout = heartbeat_interval if heartbeat_interval is not None and not hitl.is_pending() else None
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    if producer.done():
                        continue
                    await queue.put({"type": "heartbeat"})
                    continue
                if item is SENTINEL:
                    break
                recorder.observe(item)  # 累积 AI 产出用于落库
                yield _sse(item)

            # SENTINEL received — surface producer outcome
            try:
                await producer
            except asyncio.CancelledError:
                logger.info("stream cancelled session=%s", session_id)
                yield _sse({"type": "limit", "reason": "cancelled",
                            "message": "cancelled by client"})
                yield _sse({"type": "done"})
                return
            except BudgetExceededError as exc:
                logger.info("stream budget hit session=%s reason=%s",
                            session_id, exc.reason)
                yield _sse({"type": "limit", "reason": exc.reason, "message": exc.message})
                yield _sse({"type": "done"})
                return
            except GraphRecursionError as exc:
                logger.info("stream recursion limit session=%s", session_id)
                yield _sse({"type": "limit", "reason": "recursion", "message": str(exc)})
                yield _sse({"type": "done"})
                return
            except Exception as exc:
                if _is_content_moderation_error(exc):
                    logger.info("stream content moderation block session=%s", session_id)
                    yield _sse({
                        "type": "limit",
                        "reason": "content_filter",
                        "message": (
                            "内容被模型服务的安全审核拦截。这类限制由模型服务方"
                            "（通义千问/DashScope）在合规框架下施加，常见于政治/地缘、"
                            "金融投资建议（如股市预测）等敏感话题；请换个问法或更换话题。"
                        ),
                    })
                    yield _sse({"type": "done"})
                    return
                logger.error("stream error session=%s: %s", session_id, exc, exc_info=exc)
                yield _sse({"type": "error", "message": str(exc)})
                return

            elapsed = time.monotonic() - t0
            logger.info("stream done session=%s elapsed=%.2fs", session_id, elapsed)
            yield _sse({"type": "done"})
        finally:
            session["active_task"] = None
            if not producer.done():
                producer.cancel()
            # AI 产出落库（含被取消/中止/客户端断开时的部分产出）。
            if persist:
                rows = recorder.rows()
                if rows:
                    await run_in_threadpool(db.append_messages, session_id, user_id, rows)
                # 本轮 token 用量累加到 chat_session（每轮一次，不重复计）。
                await run_in_threadpool(
                    db.add_session_usage, session_id,
                    tracker.input_tokens, tracker.output_tokens, tracker.total_tokens,
                )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # 禁止代理缓冲，保证 SSE 逐条实时下发（nginx 认 X-Accel-Buffering）。
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
