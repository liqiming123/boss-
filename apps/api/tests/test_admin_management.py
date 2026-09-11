from urllib.parse import parse_qs, urlparse

from conftest import login
from sqlalchemy import select
from test_api_flow import context, message_sent

from recruitment_collab.api import routes
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.feishu import FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import CandidateSource, Recruiter, RecruiterAccessProfile, RecruitmentAccount, SystemResetJob


def real_settings():
    return get_settings().model_copy(update={"app_env": "test", "feishu_mode": "real", "feishu_app_id": "app", "feishu_app_secret": "secret", "feishu_redirect_uri": "http://test/api/v1/auth/feishu/callback"})


def feishu_login_as(client, monkeypatch, open_id: str, user_id: str, display_name: str):
    async def exchange(_self, _code):
        return FeishuIdentity(open_id=open_id, user_id=user_id, display_name=display_name)

    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)


def test_grant_admin_promotes_any_feishu_member_even_before_first_login(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    admin = login(client, "admin@example.com")
    granted = client.put("/api/v1/admin/admins", headers=admin, json={"feishu_open_id": "ou-new-admin", "feishu_user_id": "u-new-admin", "feishu_display_name": "新管理员"})
    assert granted.status_code == 200, granted.text
    body = granted.json()
    assert body["data_scope"] == "COMPANY"
    assert body["can_manage_team"] and body["can_manage_feishu"] and body["can_manage_jobs"] and body["can_reset_system"]

    # The member was promoted before ever logging in; their first web login
    # must land directly on the administrator workspace.
    feishu_login_as(client, monkeypatch, "ou-new-admin", "u-new-admin", "新管理员")
    started = client.post("/api/v1/auth/feishu/web/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    assert client.get("/api/v1/auth/feishu/callback", params={"code": "new-admin-code", "state": state}, follow_redirects=False).status_code == 303
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "ADMIN"
    assert me["workspace"]["status"] == "ADMIN"
    assert me["capabilities"]["can_manage_team"] is True

    # Multiple administrators coexist; none of them is a fixed identity.
    second = client.put("/api/v1/admin/admins", headers=admin, json={"feishu_open_id": "ou-second-admin", "feishu_display_name": "第二位管理员"})
    assert second.status_code == 200, second.text
    assert second.json()["recruiter_id"] != granted.json()["recruiter_id"]
    profiles = client.get("/api/v1/admin/access-profiles", headers=admin).json()
    admin_rows = [row for row in profiles if row["can_manage_team"] and row["can_reset_system"]]
    assert len(admin_rows) >= 2


def test_revoke_admin_resets_capabilities_and_refuses_self(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    admin = login(client, "admin@example.com")
    granted = client.put("/api/v1/admin/admins", headers=admin, json={"feishu_open_id": "ou-temp-admin", "feishu_display_name": "临时管理员"})
    recruiter_id = granted.json()["recruiter_id"]

    revoked = client.delete(f"/api/v1/admin/admins/{recruiter_id}", headers=admin)
    assert revoked.status_code == 200
    profile = session.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.recruiter_id == recruiter_id))
    assert profile is not None
    assert profile.data_scope == "OWN"
    assert not (profile.can_manage_team or profile.can_manage_feishu or profile.can_manage_jobs or profile.can_reset_system)
    target = session.get(Recruiter, recruiter_id)
    assert target.role == "RECRUITER"

    admin_row = session.scalar(select(Recruiter).where(Recruiter.email == "admin@example.com"))
    assert client.delete(f"/api/v1/admin/admins/{admin_row.id}", headers=admin).status_code == 403
    # A regular member cannot grant or revoke administration rights.
    member = login(client, "xie@example.com")
    assert client.put("/api/v1/admin/admins", headers=member, json={"feishu_open_id": "ou-x", "feishu_display_name": "X"}).status_code == 403
    assert client.delete(f"/api/v1/admin/admins/{recruiter_id}", headers=member).status_code == 403


def test_system_reset_keeps_granted_admins_who_never_logged_in(client, session):
    admin = login(client, "admin@example.com")
    granted = client.put("/api/v1/admin/admins", headers=admin, json={"feishu_open_id": "ou-kept-admin", "feishu_display_name": "待保留管理员"})
    assert granted.status_code == 200, granted.text

    preview = client.get("/api/v1/admin/system-reset/preview", headers=admin)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    kept_names = {identity["feishu_display_name"] or identity["display_name"] for identity in data["keep_identities"]}
    assert kept_names >= {"管理员", "待保留管理员"}

    response = client.post("/api/v1/admin/system-reset", headers=admin, json={"preview_version": data["preview_version"], "confirmation": "重新初始化同步"})
    assert response.status_code == 200, response.text
    remaining = session.scalars(select(Recruiter)).all()
    assert {recruiter.feishu_display_name or recruiter.display_name for recruiter in remaining} >= {"管理员", "待保留管理员"}
    assert not [recruiter for recruiter in remaining if recruiter.display_name in {"谢女士", "珈莉"}]


def test_system_reset_can_run_after_previous_requester_was_removed(client, session):
    admin = login(client, "admin@example.com")
    xie = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    session.add(SystemResetJob(company_id=xie.company_id, requested_by=xie.id, preview_version="old"))
    session.commit()

    preview = client.get("/api/v1/admin/system-reset/preview", headers=admin).json()
    response = client.post("/api/v1/admin/system-reset", headers=admin, json={"preview_version": preview["preview_version"], "confirmation": "重新初始化同步"})
    assert response.status_code == 200, response.text
    job = session.scalar(select(SystemResetJob))
    assert job is not None and job.requested_by != xie.id


def test_admin_console_shows_plugin_boss_sync_data_after_binding(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    admin = login(client, "admin@example.com")
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    assigned = client.put(
        f"/api/v1/admin/boss-accounts/{account.id}/assignment",
        headers=admin,
        json={"feishu_open_id": "ou-sync-member", "feishu_user_id": "u-sync-member", "feishu_display_name": "同步成员"},
    )
    assert assigned.status_code == 200, assigned.text

    # The member completes the extension login on the assigned BOSS page.
    feishu_login_as(client, monkeypatch, "ou-sync-member", "u-sync-member", "同步成员")
    started = client.post(
        "/api/v1/plugin/feishu-binding/start",
        json={"account_display_name": "谢女士", "action": "bind", "device_id": "sync-browser", "device_name": "Chrome"},
    )
    assert started.status_code == 200, started.text
    payload = started.json()
    state = parse_qs(urlparse(payload["authorization_url"]).query)["state"][0]
    callback = client.get("/api/v1/auth/feishu/callback", params={"code": "sync-code", "state": state})
    assert callback.status_code == 200, callback.text
    approved = client.post("/api/v1/auth/feishu/device/poll", json={"attempt_id": payload["attempt_id"], "poll_token": payload["poll_token"]})
    assert approved.status_code == 200
    tokens = approved.json()
    assert tokens["status"] == "APPROVED"

    # The plugin syncs one candidate from that BOSS account.
    sent = message_sent(client, {"Authorization": f"Bearer {tokens['access_token']}"}, context("同步候选人", "谢女士", "sync-source"))
    assert sent.status_code == 200, sent.text

    # The console now shows the plugin's BOSS sync data for this account.
    operations = client.get("/api/v1/admin/operations", headers=admin).json()
    member_binding = next(binding for binding in operations["bindings"] if binding["feishu_display_name"] == "同步成员")
    assert member_binding["boss_accounts"] == ["谢女士"]
    assert member_binding["source_count"] == 1
    assert [device["device_id"] for device in member_binding["devices"]] == ["sync-browser"]

    accounts = client.get("/api/v1/admin/boss-accounts", headers=admin).json()
    row = next(item for item in accounts if item["boss_account"] == "谢女士")
    assert row["status"] == "ASSIGNED"
    assert row["source_count"] == 1
    assert row["active_device_count"] == 1


def test_admin_and_recruiter_data_boundaries_with_temporary_boss_account(client, session):
    """A temporary second BOSS account proves the company/own boundary.

    The account is explicitly removed before the test ends; the isolated
    database fixture is an additional cleanup boundary.
    """
    admin = login(client, "admin@example.com")
    xie = session.scalar(select(Recruiter).where(Recruiter.email == "xie@example.com"))
    jiali = session.scalar(select(Recruiter).where(Recruiter.email == "jiali@example.com"))
    own_account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.recruiter_id == xie.id))
    created = client.post(
        "/api/v1/admin/accounts",
        headers=admin,
        json={
            "recruiter_id": jiali.id,
            "platform": "boss",
            "platform_account_key": "permission-boundary-temp",
            "account_display_name": "权限边界测试账号",
        },
    )
    assert created.status_code == 200, created.text
    temporary_account_id = created.json()["id"]

    own_source = CandidateSource(
        company_id=xie.company_id,
        platform="boss",
        platform_account_id=own_account.id,
        source_identity_key="boundary-own-source",
        source_identity_type="PLATFORM_ID",
        page_url_hash="boundary-own-page",
        candidate_display_name="本人候选人",
        candidate_normalized_name="本人候选人",
        raw_job_name="短视频编导",
        extractor_version="test",
    )
    other_source = CandidateSource(
        company_id=xie.company_id,
        platform="boss",
        platform_account_id=temporary_account_id,
        source_identity_key="boundary-other-source",
        source_identity_type="PLATFORM_ID",
        page_url_hash="boundary-other-page",
        candidate_display_name="其他账号候选人",
        candidate_normalized_name="其他账号候选人",
        raw_job_name="AI 应用开发工程师",
        extractor_version="test",
    )
    session.add_all([own_source, other_source])
    session.commit()

    admin_candidates = client.get("/api/v1/admin/candidate-sources?limit=1&offset=0", headers=admin)
    assert admin_candidates.status_code == 200
    assert len(admin_candidates.json()) == 1
    all_admin_candidates = client.get("/api/v1/admin/candidate-sources?limit=500&offset=0", headers=admin).json()
    assert {row["id"] for row in all_admin_candidates} >= {own_source.id, other_source.id}

    recruiter = login(client, "xie@example.com")
    own_candidates = client.get("/api/v1/admin/candidate-sources", headers=recruiter).json()
    assert {row["id"] for row in own_candidates} == {own_source.id}
    assert client.get("/api/v1/admin/boss-accounts", headers=recruiter).status_code == 403

    # Explicit cleanup mirrors the production workflow without touching any
    # production account or candidate data.
    session.delete(other_source)
    session.commit()
    removed = client.delete(f"/api/v1/admin/accounts/{temporary_account_id}", headers=admin)
    assert removed.status_code == 200, removed.text
    assert session.get(RecruitmentAccount, temporary_account_id) is None


def test_partial_admin_profiles_and_self_demotion_are_rejected(client, session):
    admin = login(client, "admin@example.com")
    admin_row = session.scalar(select(Recruiter).where(Recruiter.email == "admin@example.com"))
    xie = session.scalar(select(Recruiter).where(Recruiter.email == "xie@example.com"))
    xie_profile = RecruiterAccessProfile(company_id=xie.company_id, recruiter_id=xie.id, data_scope="OWN")
    admin_profile = RecruiterAccessProfile(
        company_id=admin_row.company_id,
        recruiter_id=admin_row.id,
        data_scope="COMPANY",
        can_manage_team=True,
        can_manage_feishu=True,
        can_manage_jobs=True,
        can_reset_system=True,
    )
    session.add_all([xie_profile, admin_profile])
    session.commit()

    partial = client.patch(
        f"/api/v1/admin/access-profiles/{xie.id}",
        headers=admin,
        json={"data_scope": "COMPANY", "can_manage_team": True},
    )
    assert partial.status_code == 400
    assert partial.json()["error"]["code"] == "PARTIAL_ADMIN_PROFILE_NOT_ALLOWED"
    session.refresh(xie_profile)
    assert xie_profile.data_scope == "OWN" and not xie_profile.can_manage_team

    self_demote = client.patch(
        f"/api/v1/admin/access-profiles/{admin_row.id}",
        headers=admin,
        json={
            "data_scope": "OWN",
            "can_manage_team": False,
            "can_manage_feishu": False,
            "can_manage_jobs": False,
            "can_reset_system": False,
        },
    )
    assert self_demote.status_code == 403
    assert self_demote.json()["error"]["code"] == "ADMIN_SELF_REVOKE_FORBIDDEN"
