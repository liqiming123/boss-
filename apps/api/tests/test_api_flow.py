import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from conftest import login
from sqlalchemy import func, select

from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.normalization import candidate_identity_signature
from recruitment_collab.infrastructure.models import (
    CandidateSource,
    CandidateSyncOutbox,
    Conflict,
    DuplicateLookupAlert,
    Engagement,
    JobAlias,
    NotificationOutbox,
    PluginDevice,
    Recruiter,
    RecruitmentEvent,
    RecruitmentJob,
    UnmappedJob,
)
from recruitment_collab.infrastructure.security import hash_token, make_token


def context(name: str, account: str, suffix: str):
    return {
        "platform": "mock",
        "page_url": f"http://localhost:5174/candidate/{suffix}",
        "platform_candidate_id": None,
        "platform_id_scope": "UNKNOWN",
        "candidate_display_name": name,
        "candidate_age": 28,
        "candidate_experience": "6年",
        "candidate_education": "本科",
        "job_display_name": "短视频编导",
        "account_display_name": account,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "client_event_id": str(uuid.uuid4()),
        "extractor_version": "mock-adapter-1",
    }


def message_sent(client, headers, payload, sent_at="2026-09-02T01:00:00Z"):
    return client.post("/api/v1/plugin/engagements/message-sent", headers=headers, json={**payload, "sent_at": sent_at})


def test_opening_candidate_checks_without_creating_records(client, session):
    headers = login(client, "xie@example.com")
    response = client.post("/api/v1/plugin/context/check", headers=headers, json=context("小明", "谢女士", "xie"))
    assert response.status_code == 200
    assert response.json()["result_type"] == "CHECK_ONLY_NO_HISTORY"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 0


def test_boss_native_history_is_confirmed_without_creating_a_feishu_row(client, session):
    payload = context("振理", "成珈莉", "native-history")
    payload["native_communications"] = [
        {"recruiter_name": "王文懋", "job_name": "总经理助理", "contacted_at": "2026-08-05T18:27:00+08:00", "source": "BOSS_NATIVE"},
        {"recruiter_name": "成珈莉", "job_name": "业务助理", "contacted_at": "2026-08-05T18:30:00+08:00", "source": "BOSS_NATIVE"},
    ]
    response = client.post("/api/v1/plugin/context/check", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["result_type"] == "CONFIRMED_DUPLICATE"
    assert [(item["recruiter_name"], item["job_name"], item["stage"]) for item in data["matches"]] == [("王文懋", "总经理助理", "飞书未同步")]
    assert data["matches"][0]["evidence_source"] == "BOSS_NATIVE"
    assert data["matches"][0]["feishu_synced"] is False
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 0


def test_boss_native_history_enriches_matching_system_history_without_duplication(client):
    assert message_sent(client, {}, context("振理", "王文懋", "native-feishu")).status_code == 200
    payload = context("振理", "成珈莉", "native-feishu-check")
    payload["native_communications"] = [
        {"recruiter_name": "王文懋", "job_name": "短视频编导", "contacted_at": "2026-08-05T18:27:00+08:00", "source": "BOSS_NATIVE"}
    ]
    response = client.post("/api/v1/plugin/context/check", json=payload)
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["recruiter_name"] == "王文懋"
    assert matches[0]["evidence_source"] == "BOSS_AND_FEISHU"
    assert matches[0]["feishu_synced"] is True


def test_incomplete_four_field_identity_can_only_be_same_name_job_suspected(client, session):
    first = message_sent(client, {}, context("同名候选人", "谢女士", "first"))
    assert first.status_code == 200
    incomplete = context("同名候选人", "珈莉", "second")
    incomplete["candidate_education"] = None
    result = client.post("/api/v1/plugin/context/check", json=incomplete)
    assert result.status_code == 200
    assert result.json()["result_type"] == "SUSPECTED_DUPLICATE"
    assert result.json()["matches"][0]["match_level"] == "SUSPECTED_SAME_NAME_JOB"


def test_click_exact_identity_immediately_queues_bilateral_messages_with_cooldown(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    recruiters = session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all()
    for recruiter in recruiters:
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    assert message_sent(client, xie, context("立即提醒候选人", "谢女士", "lookup-first")).status_code == 200
    payload = context("立即提醒候选人", "珈莉", "lookup-second")
    first = client.post("/api/v1/plugin/context/check", headers=jiali, json=payload)
    assert first.status_code == 200
    assert first.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert first.json()["lookup_notifications_queued"] == 2
    assert session.scalar(select(func.count()).select_from(DuplicateLookupAlert)) == 1
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 2
    repeat = client.post("/api/v1/plugin/context/check", headers=jiali, json={**payload, "client_event_id": str(uuid.uuid4())})
    assert repeat.status_code == 200
    assert repeat.json()["lookup_notifications_queued"] == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 2


def test_click_evidence_upgrade_bypasses_cooldown(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    first_payload = context("升级证据候选人", "谢女士", "upgrade-first")
    source_id = message_sent(client, xie, first_payload).json()["candidate_source_id"]
    current_payload = context("升级证据候选人", "珈莉", "upgrade-second")
    current_payload["candidate_age"] = 31
    suspected = client.post("/api/v1/plugin/context/check", headers=jiali, json=current_payload)
    assert suspected.json()["result_type"] == "SUSPECTED_DUPLICATE"
    assert suspected.json()["lookup_notifications_queued"] == 2
    source = session.get(CandidateSource, source_id)
    source.candidate_age = 31
    source.candidate_identity_signature = candidate_identity_signature("升级证据候选人", 31, "6年", "本科")
    session.commit()
    upgraded = client.post("/api/v1/plugin/context/check", headers=jiali, json={**current_payload, "client_event_id": str(uuid.uuid4())})
    assert upgraded.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert upgraded.json()["lookup_notifications_queued"] == 2
    alert = session.scalar(select(DuplicateLookupAlert))
    assert alert.evidence_rank == 2 and alert.notification_version == 2
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 4


def test_boss_native_history_immediately_queues_messages_when_both_people_are_bound(client, session):
    headers = login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    payload = context("原生记录候选人", "珈莉", "native-alert")
    payload["native_communications"] = [
        {"recruiter_name": "谢女士", "job_name": "短视频编导", "contacted_at": "2026-09-01T09:00:00+08:00", "source": "BOSS_NATIVE"}
    ]
    response = client.post("/api/v1/plugin/context/check", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert response.json()["lookup_notifications_queued"] == 2
    assert session.scalar(select(DuplicateLookupAlert)).evidence_rank == 3
    repeat = client.post("/api/v1/plugin/context/check", headers=headers, json={**payload, "client_event_id": str(uuid.uuid4())})
    assert repeat.status_code == 200
    assert repeat.json()["lookup_notifications_queued"] == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 2


def test_same_recruiter_same_name_and_job_with_different_age_stays_separate(client, session):
    first = context("同名候选人", "谢女士", "first")
    second = context("同名候选人", "谢女士", "second")
    second["candidate_age"] = 31
    assert message_sent(client, {}, first).status_code == 200
    assert message_sent(client, {}, second).status_code == 200
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2


def test_snapshot_upload_keeps_only_tokens_and_requeues_candidate(client, session):
    source_id = message_sent(client, {}, context("快照候选人", "谢女士", "snapshot")).json()["candidate_source_id"]
    image = b"\xff\xd8fake-jpeg\xff\xd9"
    response = client.post(
        f"/api/v1/plugin/conversations/{source_id}/snapshot",
        data={"snapshot_hash": hashlib.sha256(image).hexdigest(), "related_source_ids": ""},
        files=[("files", ("snapshot.jpg", image, "image/jpeg"))],
    )
    assert response.status_code == 200, response.text
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.snapshot_status == "READY"
    assert source.snapshot_tokens_json == [f"mock-boss-chat-{source_id}-1.jpg"]


def test_snapshot_upload_rejects_another_recruiters_source_before_upload(client, session):
    source_id = message_sent(client, login(client, "xie@example.com"), context("归属候选人", "谢女士", "owner")).json()["candidate_source_id"]
    image = b"\xff\xd8fake-jpeg\xff\xd9"
    response = client.post(
        f"/api/v1/plugin/conversations/{source_id}/snapshot",
        headers=login(client, "jiali@example.com"),
        data={"snapshot_hash": hashlib.sha256(image).hexdigest(), "related_source_ids": ""},
        files=[("files", ("snapshot.jpg", image, "image/jpeg"))],
    )
    assert response.status_code == 403
    assert session.get(CandidateSource, source_id).snapshot_status == "NONE"


def test_resume_preview_upload_keeps_only_feishu_metadata(client, session):
    source_id = message_sent(client, {}, context("简历候选人", "谢女士", "resume")).json()["candidate_source_id"]
    resume = b"%PDF-1.7\nminimal-test-resume"
    response = client.post(
        f"/api/v1/plugin/conversations/{source_id}/resume",
        data={"resume_hash": hashlib.sha256(resume).hexdigest(), "related_source_ids": ""},
        files={"file": ("candidate.pdf", resume, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.resume_status == "READY"
    assert source.resume_tokens_json == ["mock-resume-candidate.pdf"]
    assert source.resume_hash == hashlib.sha256(resume).hexdigest()
    assert source.resume_file_name == "candidate.pdf"
    queued = session.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.candidate_source_id == source_id))
    assert queued is not None
    assert queued.payload_json["简历附件"] == [{"file_token": "mock-resume-candidate.pdf"}]


@pytest.mark.parametrize(
    ("file_name", "content_type", "content"),
    [
        ("candidate.pdf", "application/pdf", b"%PDF-1.7\nresume"),
        ("candidate.doc", "application/msword", b"word-resume"),
        (
            "candidate.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"docx-resume",
        ),
        ("candidate.jpg", "image/jpeg", b"\xff\xd8resume\xff\xd9"),
        ("candidate.png", "image/png", b"\x89PNG\r\n\x1a\nresume"),
    ],
)
def test_resume_preview_accepts_supported_formats(client, session, file_name, content_type, content):
    source_id = message_sent(client, {}, context(f"格式候选人-{file_name}", "谢女士", file_name)).json()["candidate_source_id"]
    response = client.post(
        f"/api/v1/plugin/conversations/{source_id}/resume",
        data={"resume_hash": hashlib.sha256(content).hexdigest(), "related_source_ids": ""},
        files={"file": (file_name, content, content_type)},
    )
    assert response.status_code == 200, response.text
    assert session.get(CandidateSource, source_id).resume_status == "READY"


def test_scan_checkpoint_defaults_to_48_hours_and_never_moves_backwards(client):
    initial = client.get("/api/v1/plugin/conversations/checkpoint", params={"account_display_name": "谢女士"})
    assert initial.status_code == 200 and initial.json()["initial"] is True
    partial = {"platform": "boss", "account_display_name": "谢女士", "completed_through_at": "2026-09-02T03:00:00Z", "cursor": {"candidate": "甲"}}
    rejected = client.put("/api/v1/plugin/conversations/checkpoint", json=partial)
    assert rejected.status_code == 200 and rejected.json()["accepted"] is False
    assert client.get("/api/v1/plugin/conversations/checkpoint", params={"account_display_name": "谢女士"}).json()["initial"] is True
    later = {**partial, "completed_through_at": "2026-09-02T02:00:00Z", "cursor": {"scanned": 3, "complete": True}}
    accepted = client.put("/api/v1/plugin/conversations/checkpoint", json=later)
    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    earlier = {**later, "completed_through_at": "2026-09-01T02:00:00Z", "cursor": {"scanned": 2, "complete": True}}
    response = client.put("/api/v1/plugin/conversations/checkpoint", json=earlier)
    assert response.status_code == 200
    assert response.json()["completed_through_at"].startswith("2026-09-02T02:00:00")


def test_unmapped_recruitment_account_does_not_block_candidate_flow(client):
    headers = login(client, "xie@example.com")
    response = message_sent(client, headers, context("小明", "无需绑定的页面账号", "unmapped-account"))
    assert response.status_code == 200
    data = response.json()
    assert data["result_type"] == "NO_HISTORY"
    assert data["account_mapping"]["status"] == "MAPPED"
    assert data["feishu_candidate_fields"]["当前招聘者"] == "无需绑定的页面账号"
    event = client.post(
        "/api/v1/plugin/events",
        headers=headers,
        json={"candidate_source_id": data["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": str(uuid.uuid4())},
    )
    assert event.status_code == 200


def test_unmapped_job_still_records_sent_message_and_raw_boss_job(client, session):
    headers = login(client, "xie@example.com")
    payload = context("未映射岗位候选人", "谢女士", "unmapped-job")
    payload["job_display_name"] = "全新 BOSS 岗位"
    response = message_sent(client, headers, payload)
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_source_id"]
    assert data["job_mapping"] == {"status": "JOB_UNMAPPED"}
    assert data["feishu_candidate_fields"]["BOSS岗位"] == "全新 BOSS 岗位"
    assert data["feishu_candidate_fields"]["标准岗位"] == ""
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 1
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_mapping_an_unmapped_job_backfills_existing_business_rows(client, session):
    payload = context("回填岗位候选人", "谢女士", "backfill")
    payload["job_display_name"] = "待回填岗位"
    source_id = message_sent(client, login(client, "xie@example.com"), payload).json()["candidate_source_id"]
    unmapped = session.scalar(select(UnmappedJob).where(UnmappedJob.normalized_job_name == "待回填岗位"))
    job = session.scalar(select(RecruitmentJob))
    response = client.patch(f"/api/v1/admin/unmapped-jobs/{unmapped.id}", headers=login(client, "admin@example.com"), json={"job_id": job.id})
    assert response.status_code == 200
    assert response.json()["backfilled_candidate_count"] == 1
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.job_id == job.id
    assert session.scalar(select(Engagement).where(Engagement.candidate_source_id == source_id)).job_id == job.id
    assert session.scalar(select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id)).job_id == job.id
    assert (
        session.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.candidate_source_id == source_id)).payload_json["标准岗位"] == job.canonical_name
    )


def test_same_platform_candidate_on_two_jobs_creates_two_rows(client, session):
    company_id = session.scalar(select(RecruitmentJob)).company_id
    second_job = RecruitmentJob(company_id=company_id, code="SECOND", canonical_name="第二岗位", category="其他")
    session.add(second_job)
    session.flush()
    session.add(JobAlias(company_id=company_id, job_id=second_job.id, platform="mock", raw_alias="第二岗位", normalized_alias="第二岗位"))
    session.commit()
    headers = login(client, "xie@example.com")
    first = context("平台候选人", "谢女士", "platform-job-1")
    first["platform_candidate_id"] = "pid-one"
    second = context("平台候选人", "谢女士", "platform-job-2")
    second.update({"platform_candidate_id": "pid-one", "job_display_name": "第二岗位"})
    first_id = message_sent(client, headers, first).json()["candidate_source_id"]
    second_id = message_sent(client, headers, second).json()["candidate_source_id"]
    assert first_id != second_id
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2


def test_status_only_advances_on_stronger_explicit_evidence(client, session):
    headers = login(client, "xie@example.com")
    payload = context("状态候选人", "谢女士", "status")
    first = message_sent(client, headers, {**payload, "recruitment_status": "已约面", "status_evidence": "BOSS_INTERVIEW_MARKER"})
    assert first.status_code == 200
    payload["client_event_id"] = str(uuid.uuid4())
    lower = message_sent(client, headers, {**payload, "recruitment_status": "已获取简历", "status_evidence": "BOSS_RESUME_MARKER"})
    assert lower.status_code == 200
    source = session.get(CandidateSource, first.json()["candidate_source_id"])
    assert source.recruitment_status == "已约面"
    payload["client_event_id"] = str(uuid.uuid4())
    terminal = message_sent(client, headers, {**payload, "recruitment_status": "已拒绝", "status_evidence": "EXPLICIT_REJECTION"})
    assert terminal.status_code == 200
    payload["client_event_id"] = str(uuid.uuid4())
    assert message_sent(client, headers, {**payload, "recruitment_status": "沟通中", "status_evidence": "RECRUITER_OUTBOUND"}).status_code == 200
    assert session.get(CandidateSource, first.json()["candidate_source_id"]).recruitment_status == "已拒绝"


def test_v3_neutral_evidence_repairs_a_legacy_false_rejection(client, session):
    headers = login(client, "xie@example.com")
    payload = context("旧误拒绝候选人", "谢女士", "legacy-rejection")
    first = message_sent(
        client,
        headers,
        {
            **payload,
            "recruitment_status": "已拒绝",
            "status_evidence": "EXPLICIT_REJECTION",
            "status_rule_version": "boss-status-v2",
        },
    )
    source_id = first.json()["candidate_source_id"]
    payload["client_event_id"] = str(uuid.uuid4())
    repaired = message_sent(
        client,
        headers,
        {
            **payload,
            "recruitment_status": "沟通中",
            "status_evidence": "RECRUITER_OUTBOUND",
            "status_rule_version": "boss-status-v3",
        },
        sent_at="2026-09-02T02:00:00Z",
    )
    assert repaired.status_code == 200
    source = session.get(CandidateSource, source_id)
    assert source.recruitment_status == "沟通中"
    assert source.status_rule_version == "boss-status-v3"


def test_snapshot_failure_status_does_not_replace_ready_attachment(client, session):
    source_id = message_sent(client, {}, context("快照状态候选人", "谢女士", "snapshot-status")).json()["candidate_source_id"]
    failed = client.put(
        "/api/v1/plugin/conversations/snapshot-status",
        json={"candidate_source_ids": [source_id], "status": "FAILED", "error_code": "SNAPSHOT_EMPTY"},
    )
    assert failed.status_code == 200
    assert session.get(CandidateSource, source_id).snapshot_status == "FAILED"
    session.get(CandidateSource, source_id).snapshot_status = "READY"
    session.commit()
    interrupted = client.put(
        "/api/v1/plugin/conversations/snapshot-status",
        json={"candidate_source_ids": [source_id], "status": "INTERRUPTED", "error_code": "SNAPSHOT_INTERRUPTED_BY_USER"},
    )
    assert interrupted.status_code == 200
    assert session.get(CandidateSource, source_id).snapshot_status == "READY"


def test_reopening_same_boss_candidate_updates_one_source_record(client, session):
    job = session.scalar(select(RecruitmentJob))
    session.add(JobAlias(company_id=job.company_id, job_id=job.id, platform="boss", raw_alias="短视频编导", normalized_alias="短视频编导"))
    session.commit()
    headers = login(client, "xie@example.com")
    payload = context("王照亭", "李先生", "boss-chat")
    payload["platform"] = "boss"
    payload["page_url"] = "https://www.zhipin.com/web/chat/index"
    first = message_sent(client, headers, payload).json()
    payload["client_event_id"] = str(uuid.uuid4())
    second = message_sent(client, headers, payload, "2026-09-02T02:00:00Z").json()
    assert second["candidate_source_id"] == first["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert second["feishu_candidate_fields"]["当前招聘者"] == "李先生"
    queued = session.scalar(select(CandidateSyncOutbox))
    assert queued is not None
    assert queued.payload_version == 2
    assert queued.payload_json["更新时间"] == 1788314400000


def test_plugin_context_does_not_require_internal_login_token(client, session):
    payload = context("无令牌候选人", "李先生", "no-token")
    checked = client.post("/api/v1/plugin/context/check", json=payload)
    assert checked.status_code == 200
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    sent = message_sent(client, {}, payload)
    assert sent.status_code == 200
    assert sent.json()["feishu_candidate_fields"]["当前招聘者"] == "李先生"
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_production_plugin_endpoints_require_an_active_feishu_device(client, session, monkeypatch):
    from recruitment_collab.api import dependencies, routes

    production = get_settings().model_copy(update={"app_env": "production", "feishu_mode": "mock"})
    monkeypatch.setattr(dependencies, "get_settings", lambda: production)
    monkeypatch.setattr(routes, "get_settings", lambda: production)
    payload = context("设备候选人", "谢女士", "device-auth")
    assert client.post("/api/v1/plugin/context/check", json=payload).status_code == 401
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    web_token = make_token(recruiter.id, recruiter.company_id, recruiter.role)
    assert client.post("/api/v1/plugin/context/check", headers={"Authorization": f"Bearer {web_token}"}, json=payload).status_code == 401
    refresh_token = make_token(recruiter.id, recruiter.company_id, recruiter.role, "refresh", "device-production")
    session.add(
        PluginDevice(
            company_id=recruiter.company_id,
            recruiter_id=recruiter.id,
            device_id="device-production",
            device_name="Chrome",
            refresh_token_hash=hash_token(refresh_token),
        )
    )
    session.commit()
    device_token = make_token(recruiter.id, recruiter.company_id, recruiter.role, device_id="device-production")
    response = client.post("/api/v1/plugin/context/check", headers={"Authorization": f"Bearer {device_token}"}, json=payload)
    assert response.status_code == 200
    forbidden = client.post(
        "/api/v1/plugin/context/check", headers={"Authorization": f"Bearer {device_token}"}, json={**payload, "account_display_name": "其他招聘者"}
    )
    assert forbidden.status_code == 403


def test_message_sent_is_idempotent_and_does_not_duplicate_business_events(client, session):
    headers = login(client, "xie@example.com")
    payload = context("幂等候选人", "谢女士", "idempotent")
    first = message_sent(client, headers, payload)
    repeat = message_sent(client, headers, payload)
    assert first.status_code == repeat.status_code == 200
    assert repeat.json()["idempotent"] is True
    assert repeat.json()["candidate_source_id"] == first.json()["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 1
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_boss_non_chat_page_is_ignored(client, session):
    headers = login(client, "xie@example.com")
    payload = context("不应创建", "无需绑定", "data-page")
    payload.update({"platform": "boss", "page_url": "https://www.zhipin.com/web/chat/data-recruit"})
    response = client.post("/api/v1/plugin/context/check", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["result_type"] == "IGNORED_PAGE"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0


def test_conversation_times_keep_earliest_start_and_latest_update(client, session):
    headers = login(client, "xie@example.com")
    payload = context("时间候选人", "谢女士", "conversation-times")
    payload.update({"conversation_started_at": "2026-09-02T01:00:00Z", "conversation_updated_at": "2026-09-02T02:00:00Z"})
    first = message_sent(client, headers, payload, "2026-09-02T01:00:00Z")
    assert first.status_code == 200
    payload.update({"client_event_id": str(uuid.uuid4())})
    second = message_sent(client, headers, payload, "2026-09-01T01:00:00Z")
    assert second.status_code == 200
    payload.update({"client_event_id": str(uuid.uuid4())})
    third = message_sent(client, headers, payload, "2026-09-03T02:00:00Z")
    assert third.status_code == 200
    source = session.get(CandidateSource, second.json()["candidate_source_id"])
    assert source.conversation_started_at.isoformat() == "2026-09-01T01:00:00"
    assert source.conversation_updated_at.isoformat() == "2026-09-03T02:00:00"
    fields = third.json()["feishu_candidate_fields"]
    assert fields["BOSS岗位"] == "短视频编导"
    assert fields["标准岗位"] == "短视频编导"
    assert fields["开始聊天时间"] == 1788224400000
    assert fields["更新时间"] == 1788400800000
    assert "最近更新时间" not in fields


def test_second_page_recruiter_creates_conflict_without_unmapped_direct_message(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    first = message_sent(client, xie, context("小明", "谢女士", "xie")).json()
    check = client.post("/api/v1/plugin/context/check", headers=jiali, json=context("小明", "珈莉", "jiali")).json()
    assert check["result_type"] == "CONFIRMED_DUPLICATE"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    second = message_sent(client, jiali, context("小明", "珈莉", "jiali")).json()
    assert second["result_type"] == "CONFIRMED_DUPLICATE"
    assert second["history_summary"]["multiple_recruiters"] is True
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2
    assert second["candidate_source_id"] != first["candidate_source_id"]
    assert "多人跟进" not in second["feishu_candidate_fields"]
    assert "历史状态" not in second["feishu_candidate_fields"]
    assert "其他跟进同事" not in second["feishu_candidate_fields"]
    assert session.scalar(select(func.count()).select_from(Conflict)) == 1
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
    key = str(uuid.uuid4())
    result = client.post(
        "/api/v1/plugin/events", headers=jiali, json={"candidate_source_id": second["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": key}
    )
    assert result.status_code == 200, result.text
    assert session.scalar(select(func.count()).select_from(Conflict)) == 1
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
    repeat = client.post(
        "/api/v1/plugin/events", headers=jiali, json={"candidate_source_id": second["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": key}
    )
    assert repeat.json()["idempotent"] is True
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0


def test_continue_requires_reason(client):
    headers = login(client, "xie@example.com")
    source = message_sent(client, headers, context("张女士", "谢女士", "reason")).json()
    response = client.post(
        "/api/v1/plugin/events",
        headers=headers,
        json={"candidate_source_id": source["candidate_source_id"], "event_type": "CONTINUED_AFTER_WARNING", "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REASON_REQUIRED"
