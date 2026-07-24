"""Authentication, SSO integration, and user profile endpoints."""

import os
from urllib.parse import urlparse

import httpx
import oss2
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from hylian_client.sdk import context
from hylian_client.sdk.fastapi import enable_hylian_shield
from pydantic import BaseModel

from logger import get_logger

logger = get_logger("web_api.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

hylian_client = None
session_manager = None

_OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "")
_OSS_BUCKET_NAME = os.getenv("OSS_BUCKET", "")
_OSS_URL_EXPIRE_SECONDS = int(os.getenv("OSS_URL_EXPIRE_SECONDS", "3600"))
_SESSION_COOKIE = "HYLIAN_JSESSIONID"


def _make_oss_bucket() -> oss2.Bucket | None:
    key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    if not (_OSS_ENDPOINT and _OSS_BUCKET_NAME and key_id and key_secret):
        logger.warning("OSS 未配置完整，头像加签将退化为原样返回")
        return None
    return oss2.Bucket(oss2.Auth(key_id, key_secret), _OSS_ENDPOINT, _OSS_BUCKET_NAME)


_oss_bucket = _make_oss_bucket()


def configure_auth(app: FastAPI) -> None:
    """Install Hylian middleware and initialize shared authentication clients."""
    global hylian_client, session_manager
    hylian_client = enable_hylian_shield(app, cors_origins=["http://localhost:5173"])
    session_manager = app.state.hylian_session_manager
    hylian_client._client = httpx.Client(
        verify=hylian_client.config.verify_tls,
        timeout=hylian_client.config.timeout,
        trust_env=False,
    )


def sign_avatar(avatar: str | None) -> str | None:
    if not avatar or _oss_bucket is None:
        return avatar
    key = urlparse(avatar).path.lstrip("/") if avatar.startswith(("http://", "https://")) else avatar
    if not key:
        return avatar
    try:
        return _oss_bucket.sign_url("GET", key, _OSS_URL_EXPIRE_SECONDS, slash_safe=True)
    except Exception:
        logger.exception("头像 OSS 加签失败，返回原始 avatar")
        return avatar


def _hylian_request(method: str, path: str, **kwargs) -> httpx.Response:
    cfg = hylian_client.config
    with httpx.Client(verify=cfg.verify_tls, timeout=cfg.timeout, trust_env=False) as client:
        return client.request(method, f"{cfg.server_url}{path}", **kwargs)


def current_user_id() -> str | None:
    user = context.get_user()
    return user.id if user is not None else None


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha: str


@router.get("/captcha")
def auth_captcha(response: Response) -> dict:
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


@router.post("/login")
def auth_login(body: LoginRequest, request: Request) -> Response:
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
    session = request.state.hylian_session
    session_manager.mark_token_session(session)
    session.set_token(token)
    session.user = hylian_client.get_user(token)
    session_manager.touch(session)
    resp = JSONResponse({"ok": True})
    for raw in r.headers.get_list("set-cookie"):
        if raw.startswith(("TICKET=", "TOKEN=")):
            resp.raw_headers.append((b"set-cookie", raw.encode("latin-1")))
    return resp


@router.post("/logout")
def auth_logout(request: Request) -> Response:
    session_manager.invalidate(request.state.hylian_session.sid)
    resp = JSONResponse({"logout_url": hylian_client.logout_redirect_url()})
    resp.delete_cookie(hylian_client.config.session_cookie_name, path="/")
    resp.delete_cookie(_SESSION_COOKIE, path="/api/auth")
    return resp


@router.get("/me")
def auth_me() -> dict:
    user = context.get_user()
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return {
        "username": user.username,
        "name": user.name or user.username,
        "avatar": sign_avatar(user.avatar),
        "email": user.email,
        "phone": user.phone,
        "company": user.company,
        "position": user.position,
        "industry": user.industry,
        "location": " ".join(p for p in (user.province, user.city, user.district) if p) or None,
        "tenant": user.tenant.name if user.tenant else None,
        "super_admin": user.super_admin,
        "register_time": user.create_time,
    }
