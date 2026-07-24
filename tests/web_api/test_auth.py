"""Authentication API tests."""

from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from web_api import auth


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
            patch("web_api.runtime.create_agent", return_value=mock_react_agent),
            patch("web_api.runtime.create_plan_execute_agent", return_value=mock_plan_execute_agent),
            patch("web_api.auth._hylian_request", return_value=login_resp),
            patch.object(auth.hylian_client, "get_user", return_value=MagicMock()),
        ):
            # 不带 Authorization 头：纯 cookie/session 模式。
            with TestClient(server.app, raise_server_exceptions=False) as c:
                sid_name = auth.hylian_client.config.session_cookie_name

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

