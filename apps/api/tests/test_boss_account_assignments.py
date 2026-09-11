from urllib.parse import parse_qs, urlparse

from conftest import login
from sqlalchemy import select

from recruitment_collab.api import routes
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.feishu import FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import BossAccountAssignment, CandidateSource, CandidateSyncOutbox, PluginDevice, Recruiter, RecruitmentAccount


def real_settings():
    return get_settings().model_copy(update={"app_env": "test", "feishu_mode": "real", "feishu_app_id": "app", "feishu_app_secret": "secret", "feishu_redirect_uri": "http://test/api/v1/auth/feishu/callback"})


def test_admin_replaces_boss_owner_and_revokes_old_device(client, session):
    admin = login(client, "admin@example.com")
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    old_device = PluginDevice(company_id=account.company_id, recruiter_id=account.recruiter_id, device_id="old-device-123", device_name="Chrome", refresh_token_hash="old")
    session.add(old_device); session.commit()

    response = client.put(f"/api/v1/admin/boss-accounts/{account.id}/assignment", headers=admin, json={"feishu_open_id":"ou-new-owner", "feishu_user_id":"u-new-owner", "feishu_display_name":"新负责人"})
    assert response.status_code == 200, response.text
    assert response.json()["inherited_candidate_count"] == 0
    session.refresh(old_device); session.refresh(account)
    assignment = session.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE"))
    assert assignment and assignment.feishu_display_name == "新负责人"
    assert account.recruiter_id == assignment.feishu_recruiter_id
    assert old_device.status == "REVOKED" and old_device.refresh_token_hash is None

    other = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.id != account.id))
    conflict = client.put(f"/api/v1/admin/boss-accounts/{other.id}/assignment", headers=admin, json={"feishu_open_id":"ou-new-owner", "feishu_user_id":"u-new-owner", "feishu_display_name":"新负责人"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "FEISHU_ALREADY_ASSIGNED"


def test_unassigned_feishu_member_can_enter_console_but_has_no_boss_scope(client, session, monkeypatch, tmp_path):
    settings = real_settings().model_copy(update={"extension_package_path": str(tmp_path / "missing.zip"), "extension_release_metadata_path": str(tmp_path / "missing.json")})
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-unassigned", user_id="u-unassigned", display_name="未分配成员")
    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post("/api/v1/auth/feishu/web/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    response = client.get("/api/v1/auth/feishu/callback", params={"code":"code", "state":state}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/recruitment/overview")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "未分配成员"
    assert me.json()["capabilities"]["data_scope"] == "OWN"
    assert me.json()["has_boss_assignment"] is False
    assert session.query(RecruitmentAccount).filter(RecruitmentAccount.recruiter_id == me.json()["id"]).count() == 0

    assert me.json()["workspace"]["status"] == "UNASSIGNED"
    for resource in ("operations", "candidate-sources", "interviews", "conflicts"):
        response = client.get(f"/api/v1/admin/{resource}")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "BOSS_ASSIGNMENT_REQUIRED"
    # The extension entry stays available: the package is missing in tests so
    # the endpoint must fail with the availability error, not with 401/403.
    release = client.get("/api/v1/admin/extension-release")
    assert release.status_code == 503
    assert release.json()["error"]["code"] == "EXTENSION_RELEASE_UNAVAILABLE"


def test_assigned_member_sees_own_boss_account_after_admin_assignment(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-assigned", user_id="u-assigned", display_name="待分配成员")
    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post("/api/v1/auth/feishu/web/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    assert client.get("/api/v1/auth/feishu/callback", params={"code":"code", "state":state}, follow_redirects=False).status_code == 303
    member = client.get("/api/v1/auth/me").json()
    assert member["has_boss_assignment"] is False
    assert client.get("/api/v1/admin/operations").status_code == 403

    admin = login(client, "admin@example.com")
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    source = CandidateSource(company_id=account.company_id, platform="mock", platform_account_id=account.id,
        source_identity_key="inherited-row", source_identity_type="PLATFORM_ID", page_url_hash="page-hash",
        candidate_display_name="历史候选人", candidate_normalized_name="历史候选人", raw_job_name="招聘岗位", extractor_version="test")
    session.add(source)
    session.commit()
    assigned = client.put(
        f"/api/v1/admin/boss-accounts/{account.id}/assignment",
        headers=admin,
        json={"feishu_open_id": "ou-assigned", "feishu_user_id": "u-assigned", "feishu_display_name": "待分配成员"},
    )
    assert assigned.status_code == 200, assigned.text

    refreshed = client.get("/api/v1/auth/me").json()
    assert refreshed["has_boss_assignment"] is True
    assert refreshed["workspace"] == {"status": "EXTENSION_REQUIRED", "boss_account_name": "谢女士"}
    assert client.get("/api/v1/admin/candidate-sources").json()["error"]["code"] == "EXTENSION_CONNECTION_REQUIRED"
    started = client.post("/api/v1/plugin/feishu-binding/start", json={"account_display_name": "谢女士", "action": "bind", "device_id": "member-browser-123", "device_name": "Chrome"}).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    assert client.get("/api/v1/auth/feishu/callback", params={"code": "device-code", "state": state}).status_code == 200
    assert client.post("/api/v1/auth/feishu/device/poll", json={"attempt_id": started["attempt_id"], "poll_token": started["poll_token"]}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["workspace"]["status"] == "READY"
    assert [row["id"] for row in client.get("/api/v1/admin/candidate-sources").json()] == [source.id]
    operations = client.get("/api/v1/admin/operations").json()
    assert [binding["boss_accounts"] for binding in operations["bindings"]] == [["谢女士"]]
    assert operations["bindings"][0]["bound"] is True
    assert operations["metrics"]["bound_recruiters"] == 1
    device = session.scalar(select(PluginDevice).where(PluginDevice.device_id == "member-browser-123"))
    device.status = "REVOKED"
    session.commit()
    assert client.get("/api/v1/auth/me").json()["workspace"]["status"] == "EXTENSION_REQUIRED"
    assert client.get("/api/v1/admin/candidate-sources").status_code == 403


def test_unassigned_member_cannot_touch_company_mutations(client, session, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", real_settings)
    async def exchange(_self, _code):
        return FeishuIdentity(open_id="ou-lurker", user_id="u-lurker", display_name="旁观成员")
    monkeypatch.setattr(FeishuOAuthClient, "exchange_code", exchange)
    started = client.post("/api/v1/auth/feishu/web/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    assert client.get("/api/v1/auth/feishu/callback", params={"code":"code", "state":state}, follow_redirects=False).status_code == 303

    owner = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.recruiter_id == owner.id))
    device = PluginDevice(company_id=account.company_id, recruiter_id=owner.id, device_id="owner-device", device_name="Chrome", refresh_token_hash="hashed")
    source = CandidateSource(
        company_id=account.company_id, platform="mock", platform_account_id=account.id,
        source_identity_key="sig-lurker", source_identity_type="PLATFORM_ID", page_url_hash="page-hash",
        candidate_display_name="他人候选人", candidate_normalized_name="他人候选人", raw_job_name="短视频编导", extractor_version="test",
    )
    session.add_all([device, source])
    session.flush()
    outbox = CandidateSyncOutbox(company_id=account.company_id, candidate_source_id=source.id, payload_json={"candidate": "他人候选人"}, status="FAILED")
    session.add(outbox)
    session.commit()

    # Every company-wide mutation must be denied for a member without an
    # assignment; the previous admin-only login silently provided this gate.
    assert client.patch("/api/v1/admin/settings", json={"notify_on_contact": False}).status_code == 403
    assert client.post(f"/api/v1/admin/devices/{device.device_id}/revoke").status_code == 403
    retried = client.post(f"/api/v1/admin/candidate-sync/{outbox.id}/retry")
    assert retried.status_code == 403
    session.refresh(outbox)
    assert outbox.status == "FAILED"


def test_own_scope_member_can_retry_their_own_sync_rows(client, session):
    owner = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.recruiter_id == owner.id))
    source = CandidateSource(
        company_id=account.company_id, platform="mock", platform_account_id=account.id,
        source_identity_key="sig-own-retry", source_identity_type="PLATFORM_ID", page_url_hash="page-hash-2",
        candidate_display_name="自己的候选人", candidate_normalized_name="自己的候选人", raw_job_name="短视频编导", extractor_version="test",
    )
    session.add(source)
    session.flush()
    outbox = CandidateSyncOutbox(company_id=account.company_id, candidate_source_id=source.id, payload_json={"candidate": "自己的候选人"}, status="FAILED")
    session.add(outbox)
    session.commit()

    response = client.post(f"/api/v1/admin/candidate-sync/{outbox.id}/retry", headers=login(client, "xie@example.com"))
    assert response.status_code == 200, response.text
    session.refresh(outbox)
    assert outbox.status == "PENDING"
