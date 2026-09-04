import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select

from recruitment_collab.api import routes
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.feishu import FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import NotificationOutbox, PluginDevice, Recruiter
from recruitment_collab.infrastructure.security import hash_token, make_token


def real_settings():
    return get_settings().model_copy(
        update={
            "app_env": "test",
            "feishu_mode": "real",
            "feishu_app_id": "app-id",
            "feishu_app_secret": "secret",
            "feishu_redirect_uri": "http://localhost:8000/api/v1/auth/feishu/callback",
        }
    )


def state_from(response) -> str:
    assert response.status_code == 200, response.text
    return parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]


def test_bind_status_and_verified_unbind(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-bound-user", user_id="u-bound-user", display_name="李启明")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    status = client.get("/api/v1/plugin/feishu-binding/status", params={"account_display_name": "李先生"})
    assert status.json() == {"account_display_name": "李先生", "bound": False, "feishu_display_name": None}

    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "bind"}))
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "bind-code", "state": state})
    assert callback.status_code == 200
    assert "飞书绑定成功" in callback.text
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "李先生"))
    assert recruiter is not None
    assert (recruiter.feishu_open_id, recruiter.feishu_user_id, recruiter.feishu_display_name) == ("ou-bound-user", "u-bound-user", "李启明")
    assert client.get("/api/v1/plugin/feishu-binding/status", params={"account_display_name": "李先生"}).json()["bound"] is True

    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "unbind"}))
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "unbind-code", "state": state})
    assert callback.status_code == 200
    assert "飞书解绑成功" in callback.text
    session.refresh(recruiter)
    assert recruiter.feishu_open_id is None


def test_unbind_rejects_a_different_feishu_user(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "bind"}))

    async def owner(_self, _code):
        return FeishuIdentity(open_id="ou-owner", user_id=None, display_name="本人")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", owner)
    assert client.get("/api/v1/auth/feishu/callback", params={"code": "bind", "state": state}).status_code == 200
    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "unbind"}))

    async def stranger(_self, _code):
        return FeishuIdentity(open_id="ou-stranger", user_id=None, display_name="其他人")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", stranger)
    response = client.get("/api/v1/auth/feishu/callback", params={"code": "unbind", "state": state})
    assert response.status_code == 403
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "李先生"))
    assert recruiter is not None and recruiter.feishu_open_id == "ou-owner"


def test_oauth_exchange_keeps_personal_token_ephemeral():
    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, request.url.path))
        if request.url.path.endswith("/app_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "app-token"})
        if request.url.path.endswith("/access_token"):
            assert request.headers["authorization"] == "Bearer app-token"
            return httpx.Response(200, json={"code": 0, "data": {"access_token": "personal-token"}})
        if request.url.path.endswith("/user_info"):
            assert request.headers["authorization"] == "Bearer personal-token"
            return httpx.Response(200, json={"code": 0, "data": {"open_id": "ou-user", "user_id": "u-user", "name": "飞书用户"}})
        raise AssertionError(request.url)

    async def exchange():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FeishuOAuthClient(real_settings(), http_client).exchange_code("authorization-code")

    assert asyncio.run(exchange()) == FeishuIdentity(open_id="ou-user", user_id="u-user", display_name="飞书用户")
    assert requested == [
        ("POST", "/open-apis/auth/v3/app_access_token/internal"),
        ("POST", "/open-apis/authen/v1/access_token"),
        ("GET", "/open-apis/authen/v1/user_info"),
    ]


def test_admin_web_login_uses_feishu_and_http_only_session_cookie(client, session, monkeypatch):
    settings = real_settings().model_copy(update={"public_web_url": "http://localhost:5173"})
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    admin = session.scalar(select(Recruiter).where(Recruiter.role == "ADMIN"))
    assert admin is not None
    admin.feishu_open_id = "ou-admin"
    admin.feishu_display_name = "开发管理员"
    session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-admin", user_id="u-admin", display_name="开发管理员")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post("/api/v1/auth/feishu/web/start")
    state = state_from(started)
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "admin-code", "state": state}, follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "http://localhost:5173/recruitment/overview"
    cookie = callback.headers["set-cookie"]
    assert "recruitment_admin_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "ADMIN"


def test_admin_web_login_rejects_non_admin_feishu_identity(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    recruiter = session.scalar(select(Recruiter).where(Recruiter.role == "RECRUITER"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-recruiter"
    session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-recruiter", user_id=None, display_name="招聘者")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    state = state_from(client.post("/api/v1/auth/feishu/web/start"))
    response = client.get("/api/v1/auth/feishu/callback", params={"code": "recruiter-code", "state": state})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_LOGIN_REQUIRED"


def test_feishu_oauth_issues_revocable_extension_device_tokens(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-device-user", user_id="u-device-user", display_name="设备用户")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post(
        "/api/v1/plugin/feishu-binding/start",
        json={"account_display_name": "李先生", "action": "bind", "device_id": "browser-device-123", "device_name": "Chrome macOS"},
    )
    state = state_from(started)
    payload = started.json()
    assert client.get("/api/v1/auth/feishu/callback", params={"code": "device-code", "state": state}).status_code == 200
    approved = client.post("/api/v1/auth/feishu/device/poll", json={"attempt_id": payload["attempt_id"], "poll_token": payload["poll_token"]})
    assert approved.status_code == 200
    tokens = approved.json()
    assert tokens["status"] == "APPROVED"
    assert tokens["access_token"] and tokens["refresh_token"]
    device = session.scalar(select(PluginDevice).where(PluginDevice.device_id == "browser-device-123"))
    assert device is not None and device.refresh_token_hash
    status = client.get(
        "/api/v1/plugin/feishu-binding/status", params={"account_display_name": "李先生"}, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert status.status_code == 200 and status.json()["bound"] is True
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"], "device_id": "browser-device-123"})
    assert refreshed.status_code == 200 and refreshed.json()["access_token"]


def test_same_feishu_user_moves_to_new_boss_account_and_logs_out_old_devices(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    old_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert old_recruiter is not None
    old_recruiter.feishu_open_id = "ou-moving-user"
    old_recruiter.feishu_user_id = "u-moving-user"
    old_recruiter.feishu_display_name = "移动用户"
    old_recruiter.role = "ADMIN"
    old_refresh = make_token(old_recruiter.id, old_recruiter.company_id, old_recruiter.role, "refresh", "moving-browser")
    old_device = PluginDevice(
        company_id=old_recruiter.company_id,
        recruiter_id=old_recruiter.id,
        device_id="moving-browser",
        device_name="Chrome macOS",
        refresh_token_hash=hash_token(old_refresh),
    )
    pending_notification = NotificationOutbox(
        company_id=old_recruiter.company_id,
        event_type="TEST",
        aggregate_type="RECRUITER",
        aggregate_id=old_recruiter.id,
        recipient_recruiter_id=old_recruiter.id,
        payload_json={"candidate": "待清理"},
        idempotency_key="moving-user-pending-notification",
    )
    session.add_all([old_device, pending_notification])
    session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-moving-user", user_id="u-moving-user", display_name="移动用户")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post(
        "/api/v1/plugin/feishu-binding/start",
        json={"account_display_name": "李先生", "action": "bind", "device_id": "moving-browser", "device_name": "Chrome macOS"},
    )
    state = state_from(started)
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "move-code", "state": state})
    assert callback.status_code == 200
    assert "已自动退出上一个 BOSS 招聘账号 谢女士" in callback.text

    new_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "李先生"))
    assert new_recruiter is not None and new_recruiter.feishu_open_id == "ou-moving-user"
    assert new_recruiter.role == "ADMIN"
    session.refresh(old_recruiter)
    session.refresh(old_device)
    session.refresh(pending_notification)
    assert old_recruiter.feishu_open_id is None
    assert old_device.status == "REVOKED" and old_device.refresh_token_hash is None
    assert pending_notification.status == "CANCELLED"
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh, "device_id": "moving-browser"}).status_code == 401

    payload = started.json()
    approved = client.post("/api/v1/auth/feishu/device/poll", json={"attempt_id": payload["attempt_id"], "poll_token": payload["poll_token"]})
    assert approved.status_code == 200
    session.refresh(old_device)
    assert old_device.recruiter_id == new_recruiter.id
    assert old_device.status == "ACTIVE" and old_device.refresh_token_hash


def test_binding_does_not_replace_a_different_feishu_user_on_target_account(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-existing-user"
    recruiter.feishu_display_name = "原绑定用户"
    session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-different-user", user_id=None, display_name="另一用户")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "谢女士", "action": "bind"}))
    response = client.get("/api/v1/auth/feishu/callback", params={"code": "different-code", "state": state})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BOSS_RECRUITER_ALREADY_BOUND"
    session.refresh(recruiter)
    assert recruiter.feishu_open_id == "ou-existing-user"
