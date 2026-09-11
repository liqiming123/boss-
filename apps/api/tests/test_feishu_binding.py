import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from recruitment_collab.api import routes
from recruitment_collab.api.dependencies import Actor
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.feishu import FeishuDirectoryClient, FeishuDirectoryError, FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import BossAccountAssignment, PluginDevice, Recruiter, RecruitmentAccount


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


def assign_existing_account(session, account_name: str, open_id: str, display_name: str):
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == account_name))
    recruiter = session.get(Recruiter, account.recruiter_id)
    recruiter.feishu_open_id = open_id
    recruiter.feishu_display_name = display_name
    session.add(BossAccountAssignment(company_id=account.company_id, boss_account_id=account.id, feishu_recruiter_id=recruiter.id, feishu_open_id=open_id, feishu_display_name=display_name))
    session.commit()
    return recruiter


def test_bind_status_and_verified_unbind(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-bound-user", user_id="u-bound-user", display_name="李启明")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    status = client.get("/api/v1/plugin/feishu-binding/status", params={"account_display_name": "李先生"})
    assert status.json() == {"account_display_name": "李先生", "bound": False, "feishu_display_name": None}

    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "bind"}))
    recruiter = assign_existing_account(session, "李先生", "ou-bound-user", "李启明")
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "bind-code", "state": state})
    assert callback.status_code == 200
    assert "扩展登录成功" in callback.text
    assert recruiter is not None
    assert (recruiter.feishu_open_id, recruiter.feishu_user_id, recruiter.feishu_display_name) == ("ou-bound-user", "u-bound-user", "李启明")
    assert client.get("/api/v1/plugin/feishu-binding/status", params={"account_display_name": "李先生"}).json()["bound"] is True

    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "unbind"}))
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "unbind-code", "state": state})
    assert callback.status_code == 200
    assert "扩展已退出" in callback.text
    session.refresh(recruiter)
    assert recruiter.feishu_open_id == "ou-bound-user"


def test_unbind_rejects_a_different_feishu_user(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "李先生", "action": "bind"}))
    assign_existing_account(session, "李先生", "ou-owner", "本人")

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


def test_directory_reads_every_user_in_the_app_scope_with_pagination():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/contact/v3/scopes"):
            if request.url.params.get("page_token"):
                return httpx.Response(200, json={"code": 0, "data": {"department_ids": [], "user_ids": ["ou-explicit"], "has_more": False}})
            return httpx.Response(200, json={"code": 0, "data": {"department_ids": ["od-parent"], "user_ids": [], "has_more": True, "page_token": "scope-page-2"}})
        if request.url.path.endswith("/departments/od-parent/children"):
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_department_id": "od-child"}], "has_more": False}})
        if request.url.path.endswith("/users/find_by_department"):
            department = request.url.params["department_id"]
            page_token = request.url.params.get("page_token")
            if department == "0":
                return httpx.Response(403, json={"code": 40004, "msg": "no dept authority"})
            if department == "od-parent" and not page_token:
                return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_id": "ou-parent-1", "name": "部门成员一"}], "has_more": True, "page_token": "users-page-2"}})
            if department == "od-parent":
                return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_id": "ou-parent-2", "name": "部门成员二"}], "has_more": False}})
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_id": "ou-child", "name": "子部门成员"}], "has_more": False}})
        if request.url.path.endswith("/users/batch"):
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_id": "ou-explicit", "name": "独立成员"}]}})
        raise AssertionError(request.url)

    async def read():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FeishuDirectoryClient(real_settings(), http_client).list_users()

    users = asyncio.run(read())
    assert {row["display_name"] for row in users} == {"部门成员一", "部门成员二", "子部门成员", "独立成员"}
    assert all(request.url.path != "/open-apis/contact/v3/departments" for request in requests)
    assert all(request.url.params.get("page_size") == "50" for request in requests if request.url.path.endswith(("/scopes", "/children", "/find_by_department")))


def test_directory_query_matches_name_aliases_and_excludes_resigned_users():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/contact/v3/scopes"):
            return httpx.Response(200, json={"code": 0, "data": {"department_ids": ["od-team"], "user_ids": [], "has_more": False}})
        if request.url.path.endswith("/departments/od-team/children"):
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        if request.url.path.endswith("/users/find_by_department"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"open_id": "ou-match", "name": "Alice", "en_name": "Recruiting Alice"},
                            {"open_id": "ou-resigned", "name": "Alice离职", "status": {"is_resigned": True}},
                        ],
                        "has_more": False,
                    },
                },
            )
        if request.url.path.endswith("/users/batch"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        raise AssertionError(request.url)

    async def read():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FeishuDirectoryClient(real_settings(), http_client).list_users("recruiting")

    assert asyncio.run(read()) == [{"open_id": "ou-match", "user_id": None, "display_name": "Alice"}]


def test_directory_reports_missing_basic_user_field_permission():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/contact/v3/scopes"):
            return httpx.Response(200, json={"code": 0, "data": {"department_ids": [], "user_ids": ["ou-visible"], "has_more": False}})
        if request.url.path.endswith("/users/find_by_department"):
            return httpx.Response(403, json={"code": 40004, "msg": "no root authority"})
        if request.url.path.endswith("/users/batch"):
            # This is Feishu's real response shape when the contact scope is
            # authorized but contact:user.base:readonly is not effective.
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_id": "ou-visible", "union_id": "on-visible"}]}})
        raise AssertionError(request.url)

    async def read():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FeishuDirectoryClient(real_settings(), http_client).list_users()

    with pytest.raises(FeishuDirectoryError) as raised:
        asyncio.run(read())
    assert raised.value.stage == "USER_FIELDS"
    assert raised.value.code == "NAME_PERMISSION_MISSING"


def test_real_directory_never_merges_or_falls_back_to_logged_in_cache(session, monkeypatch):
    settings = real_settings()
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    admin = session.scalar(select(Recruiter).where(Recruiter.role == "ADMIN"))
    assert admin is not None
    admin.feishu_open_id = "ou-cached-only"
    admin.feishu_display_name = "旧缓存成员"
    session.commit()
    actor = Actor(admin.id, admin.company_id, admin.role, admin.display_name)

    async def live(_self, _query):
        return [{"open_id": "ou-live", "user_id": None, "display_name": "实时成员"}]

    from recruitment_collab.infrastructure.feishu import FeishuDirectoryClient

    monkeypatch.setattr(FeishuDirectoryClient, "list_users", live)
    assert asyncio.run(routes.feishu_directory(query="", actor=actor, db=session)) == [{"open_id": "ou-live", "user_id": None, "display_name": "实时成员"}]

    async def unavailable(_self, _query):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(FeishuDirectoryClient, "list_users", unavailable)
    with pytest.raises(routes.ApplicationError) as raised:
        asyncio.run(routes.feishu_directory(query="", actor=actor, db=session))
    assert raised.value.code == "FEISHU_DIRECTORY_UNAVAILABLE"
    assert raised.value.status_code == 503

    async def missing_name_permission(_self, _query):
        raise FeishuDirectoryError("USER_FIELDS", "NAME_PERMISSION_MISSING")

    monkeypatch.setattr(FeishuDirectoryClient, "list_users", missing_name_permission)
    with pytest.raises(routes.ApplicationError) as raised:
        asyncio.run(routes.feishu_directory(query="", actor=actor, db=session))
    assert raised.value.code == "FEISHU_DIRECTORY_NAME_PERMISSION_REQUIRED"
    assert "contact:user.base:readonly" in raised.value.message


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


def test_management_web_login_accepts_active_recruiter_in_company_scope(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    recruiter = session.scalar(select(Recruiter).where(Recruiter.role == "RECRUITER"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-recruiter"
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.recruiter_id == recruiter.id))
    session.add(BossAccountAssignment(company_id=recruiter.company_id, boss_account_id=account.id, feishu_recruiter_id=recruiter.id, feishu_open_id="ou-recruiter", feishu_display_name="招聘者"))
    session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-recruiter", user_id=None, display_name="招聘者")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    state = state_from(client.post("/api/v1/auth/feishu/web/start"))
    response = client.get("/api/v1/auth/feishu/callback", params={"code": "recruiter-code", "state": state}, follow_redirects=False)
    assert response.status_code == 303


def test_first_extension_login_claims_an_unassigned_boss_account(client, session, monkeypatch):
    """A regular Feishu member can onboard without an administrator first
    creating the assignment; taking over an occupied account is still denied."""
    monkeypatch.setattr(routes, "get_settings", real_settings)

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-first-bind", user_id="u-first-bind", display_name="首次绑定成员")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post(
        "/api/v1/plugin/feishu-binding/start",
        json={"account_display_name": "首次自助绑定账号", "action": "bind", "device_id": "first-bind-browser", "device_name": "Chrome Windows"},
    )
    state = state_from(started)
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "first-bind-code", "state": state})
    assert callback.status_code == 200, callback.text
    assert "以后无需重复绑定" in callback.text

    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "首次自助绑定账号"))
    recruiter = session.scalar(select(Recruiter).where(Recruiter.feishu_open_id == "ou-first-bind"))
    assignment = session.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE"))
    assert recruiter is not None and account.recruiter_id == recruiter.id
    assert assignment is not None and assignment.feishu_recruiter_id == recruiter.id

    payload = started.json()
    approved = client.post("/api/v1/auth/feishu/device/poll", json={"attempt_id": payload["attempt_id"], "poll_token": payload["poll_token"]})
    assert approved.status_code == 200, approved.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {approved.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["workspace"] == {"status": "READY", "boss_account_name": "首次自助绑定账号"}


def test_feishu_oauth_issues_revocable_extension_device_tokens(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-device-user", user_id="u-device-user", display_name="设备用户")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post(
        "/api/v1/plugin/feishu-binding/start",
        json={"account_display_name": "李先生", "action": "bind", "device_id": "browser-device-123", "device_name": "Chrome macOS"},
    )
    assign_existing_account(session, "李先生", "ou-device-user", "设备用户")
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


def test_extension_oauth_cannot_move_assignment_between_boss_accounts(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    old_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert old_recruiter is not None
    old_recruiter.feishu_open_id = "ou-moving-user"
    old_recruiter.feishu_user_id = "u-moving-user"
    old_recruiter.feishu_display_name = "移动用户"
    old_recruiter.role = "ADMIN"
    old_account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    session.add(BossAccountAssignment(company_id=old_recruiter.company_id, boss_account_id=old_account.id, feishu_recruiter_id=old_recruiter.id, feishu_open_id="ou-moving-user", feishu_display_name="移动用户"))
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
    assert callback.status_code == 409
    assert callback.json()["error"]["code"] == "FEISHU_ALREADY_ASSIGNED"
    session.refresh(old_recruiter)
    assert old_recruiter.feishu_open_id == "ou-moving-user"


def test_binding_does_not_replace_a_different_feishu_user_on_target_account(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-existing-user"; recruiter.feishu_display_name = "原绑定用户"
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    session.add(BossAccountAssignment(company_id=recruiter.company_id, boss_account_id=account.id, feishu_recruiter_id=recruiter.id, feishu_open_id="ou-existing-user", feishu_display_name="原绑定用户")); session.commit()

    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-different-user", user_id=None, display_name="另一用户")

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    state = state_from(client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "谢女士", "action": "bind"}))
    response = client.get("/api/v1/auth/feishu/callback", params={"code": "different-code", "state": state})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BOSS_ACCOUNT_ASSIGNED_TO_OTHER"
    session.refresh(recruiter)
    assert recruiter.feishu_open_id == "ou-existing-user"
