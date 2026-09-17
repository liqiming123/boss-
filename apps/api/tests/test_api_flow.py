import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from conftest import login
from sqlalchemy import func, select

from recruitment_collab.application.collaboration import RecruitmentCollaborationService
from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.normalization import candidate_identity_signature
from recruitment_collab.infrastructure.models import (
    AuditLog,
    BossAccountAssignment,
    CandidateSource,
    CandidateSyncOutbox,
    Conflict,
    DuplicateLookupAlert,
    Engagement,
    Interview,
    JobAlias,
    NotificationOutbox,
    PluginDevice,
    Recruiter,
    RecruitmentAccount,
    RecruitmentEvent,
    RecruitmentJob,
    UnmappedJob,
)
from recruitment_collab.infrastructure.security import hash_password, hash_token, make_token


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


def conversation_sync(client, headers, payload, sent_at="2026-09-02T01:00:00Z", has_recruiter_outbound=True):
    """Seed a candidate row the way the click-sync and the history pass do.

    Since sends stopped writing rows, every test that needs an existing
    candidate record seeds it through the sync endpoint instead.
    """
    return client.post(
        "/api/v1/plugin/conversations/sync",
        headers=headers,
        json={**payload, "sent_at": sent_at, "has_recruiter_outbound": has_recruiter_outbound},
    )


def test_opening_candidate_checks_without_creating_records(client, session):
    headers = login(client, "xie@example.com")
    response = client.post("/api/v1/plugin/context/check", headers=headers, json=context("小明", "谢女士", "xie"))
    assert response.status_code == 200
    assert response.json()["result_type"] == "CHECK_ONLY_NO_HISTORY"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 0


def test_conversation_sync_persists_every_clicked_candidate_without_faking_message_event(client, session):
    first_payload = {
        **context("点击即同步", "谢女士", "clicked-first"),
        "sent_at": "2026-09-02T01:00:00Z",
        "has_recruiter_outbound": False,
        "sync_reason": "CANDIDATE_OPENED",
    }
    first = client.post("/api/v1/plugin/conversations/sync", json=first_payload)
    assert first.status_code == 200
    assert first.json()["candidate_source_id"]
    assert first.json()["sync_trigger"] == "CANDIDATE_OPENED"
    # Clicking a candidate synchronizes the shared table row, but a look is
    # still not a follow-up: no engagement, no MESSAGE_SENT and no screenshot —
    # images are captured by the history sweep, never mid-typing.
    assert first.json()["feishu_sync_status"] == "QUEUED"
    assert first.json()["snapshot_needed"] is False
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 0
    first_source = session.get(CandidateSource, first.json()["candidate_source_id"])
    first_source.snapshot_status = "READY"
    first_source.snapshot_tokens_json = ["snapshot-token"]
    session.commit()

    repeat_payload = {**first_payload, "client_event_id": str(uuid.uuid4())}
    repeat = client.post("/api/v1/plugin/conversations/sync", json=repeat_payload)
    assert repeat.status_code == 200
    # Nothing moved: re-opening the same conversation writes nothing, and the
    # row already has an image so no second capture is requested.
    assert repeat.json()["feishu_sync_status"] == "UNCHANGED"
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1

    newer_payload = {
        **first_payload,
        "client_event_id": str(uuid.uuid4()),
        "sent_at": "2026-09-02T02:00:00Z",
        "conversation_updated_at": "2026-09-02T02:00:00Z",
    }
    newer = client.post("/api/v1/plugin/conversations/sync", json=newer_payload)
    assert newer.status_code == 200
    # A newer message time refreshes the existing row instead of being dropped.
    assert newer.json()["feishu_sync_status"] == "QUEUED"
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1
    queued = session.scalar(select(CandidateSyncOutbox))
    assert queued.payload_json["更新时间"] == int(datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 0

    second_payload = {
        **context("点击即同步", "珈莉", "clicked-second"),
        "sent_at": "2026-09-02T01:05:00Z",
        "has_recruiter_outbound": False,
        "sync_reason": "CANDIDATE_OPENED",
    }
    second = client.post("/api/v1/plugin/conversations/sync", json=second_payload)
    assert second.status_code == 200
    assert second.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert second.json()["matches"][0]["recruiter_name"] == "谢女士"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 0


def test_conversation_index_returns_account_scoped_sync_anchors(client):
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        **context("手机端候选人", "谢女士", "mobile-index"),
        "platform": "mock",
        "observed_at": observed_at,
        "conversation_updated_at": observed_at,
        "sent_at": observed_at,
        "has_recruiter_outbound": True,
        "sync_reason": "CONVERSATION_UPDATED",
    }
    synced = client.post("/api/v1/plugin/conversations/sync", json=payload)
    assert synced.status_code == 200
    old_payload = {
        **context("假期后旧候选人", "谢女士", "mobile-index-old"),
        "platform": "mock",
        "observed_at": "2026-08-01T01:00:00+00:00",
        "conversation_updated_at": "2026-08-01T01:00:00+00:00",
        "sent_at": "2026-08-01T01:00:00+00:00",
        "has_recruiter_outbound": True,
        "sync_reason": "CONVERSATION_UPDATED",
    }
    assert client.post("/api/v1/plugin/conversations/sync", json=old_payload).status_code == 200

    response = client.get(
        "/api/v1/plugin/conversations/index",
        params={"account_display_name": "谢女士", "platform": "mock"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    item = next(item for item in items if item["candidate_display_name"] == "手机端候选人")
    assert item["candidate_display_name"] == "手机端候选人"
    assert item["job_display_name"] == "短视频编导"
    assert item["conversation_updated_at"] == observed_at
    assert item["created_at"]
    assert item["recruiter_account"] == "谢女士"


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
    assert {(item["recruiter_name"], item["job_name"], item["stage"]) for item in data["matches"]} == {
        ("王文懋", "总经理助理", "飞书未同步"),
        ("成珈莉", "业务助理", "飞书未同步"),
    }
    assert data["matches"][0]["evidence_source"] == "BOSS_NATIVE"
    assert data["matches"][0]["feishu_synced"] is False
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 0


def test_boss_native_history_enriches_matching_system_history_without_duplication(client):
    assert conversation_sync(client, {}, context("振理", "王文懋", "native-feishu")).status_code == 200
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


def test_incomplete_four_field_identity_does_not_match_by_name_or_job(client, session):
    first = conversation_sync(client, {}, context("同名候选人", "谢女士", "first"))
    assert first.status_code == 200
    incomplete = context("同名候选人", "珈莉", "second")
    incomplete["candidate_education"] = None
    result = client.post("/api/v1/plugin/context/check", json=incomplete)
    assert result.status_code == 200
    assert result.json()["result_type"] == "CHECK_ONLY_NO_HISTORY"
    assert result.json()["matches"] == []


def test_transient_missing_profile_fields_do_not_erase_existing_candidate_data(client, session):
    first_payload = {
        **context("资料保留候选人", "谢女士", "profile-complete"),
        "sent_at": "2026-09-02T01:00:00Z",
        "has_recruiter_outbound": False,
        "sync_reason": "CANDIDATE_OPENED",
    }
    first = client.post("/api/v1/plugin/conversations/sync", json=first_payload)
    assert first.status_code == 200
    source = session.get(CandidateSource, first.json()["candidate_source_id"])
    assert (source.candidate_age, source.candidate_experience, source.candidate_education) == (28, "6年", "本科")

    incomplete = {**first_payload, "client_event_id": str(uuid.uuid4()), "candidate_age": None, "candidate_experience": None, "candidate_education": None}
    second = client.post("/api/v1/plugin/conversations/sync", json=incomplete)
    assert second.status_code == 200
    session.refresh(source)
    assert (source.candidate_age, source.candidate_experience, source.candidate_education) == (28, "6年", "本科")


def test_click_exact_identity_warns_latest_recruiter_with_cooldown(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    recruiters = session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all()
    for recruiter in recruiters:
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    xie_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert conversation_sync(client, xie, context("立即提醒候选人", "谢女士", "lookup-first")).status_code == 200
    payload = context("立即提醒候选人", "珈莉", "lookup-second")
    first = client.post("/api/v1/plugin/context/check", headers=jiali, json=payload)
    assert first.status_code == 200
    assert first.json()["result_type"] == "CONFIRMED_DUPLICATE"
    # With no newer communication time on the viewer, the recruiter holding
    # the latest real conversation receives the single alert.
    assert first.json()["lookup_notifications_queued"] == 1
    alert = session.scalar(select(DuplicateLookupAlert))
    assert alert is not None and alert.matched_recruiter_id == xie_recruiter.id
    queued = session.scalars(select(NotificationOutbox)).all()
    assert len(queued) == 1
    assert queued[0].recipient_recruiter_id == xie_recruiter.id
    assert queued[0].payload_json["current_recruiter_status"] == "待建立跟进记录"
    assert queued[0].payload_json["candidate_status"] == "沟通中"
    repeat = client.post("/api/v1/plugin/context/check", headers=jiali, json={**payload, "client_event_id": str(uuid.uuid4())})
    assert repeat.status_code == 200
    assert repeat.json()["lookup_notifications_queued"] == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1


def test_feishu_recruiter_label_resolves_bound_identity_before_legacy_display_name(session):
    viewer = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))
    bound = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    bound.feishu_display_name = "李启明"
    bound.feishu_open_id = "open-bound"
    viewer.feishu_open_id = "open-viewer"
    legacy = Recruiter(
        company_id=viewer.company_id,
        display_name="李启明",
        email="legacy-li@example.com",
        role="RECRUITER",
        password_hash=bound.password_hash,
        feishu_open_id="open-legacy",
    )
    session.add(legacy)
    session.commit()

    match = {
        "match_level": "EXACT_IDENTITY",
        "candidate_source_id": "rec-production-row",
        "recruiter_id": None,
        "recruiter_name": "李启明",
        "job_id": None,
        "job_name": "短视频编导",
        "stage": "沟通中",
        "updated_at": datetime.now(timezone.utc).timestamp() * 1000,
        "match_reason": "姓名、年龄、工作年限、学历完全一致（飞书系统记录）",
    }
    queued = RecruitmentCollaborationService(session)._queue_lookup_alerts(
        viewer.company_id,
        viewer,
        context("真实查重候选人", "珈莉", "real-feishu-label"),
        [match],
    )
    session.commit()

    alert = session.scalar(select(DuplicateLookupAlert))
    recipients = set(session.scalars(select(NotificationOutbox.recipient_recruiter_id)).all())
    # The bound recruiter owns the latest known conversation and receives it.
    assert queued == 1
    assert alert.matched_recruiter_id == bound.id
    assert recipients == {bound.id}
    assert legacy.id not in recipients


def test_feishu_table_url_parser_accepts_base_table_link_and_rejects_ambiguous_links():
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    assert BitableSyncClient.parse_table_url("https://feishu.cn/base/appABC123?table=tblXYZ789") == ("appABC123", "tblXYZ789")
    assert BitableSyncClient.parse_table_url("https://tenant.feishu.cn/wiki/wikABC123?table=tblXYZ789&view=vew123") == (
        "wikABC123",
        "tblXYZ789",
    )
    with pytest.raises(ValueError):
        BitableSyncClient.parse_table_url("https://feishu.cn/base/appABC123")


def test_system_reset_keeps_all_administrators_and_clears_operational_rows(client, session):
    admin = session.scalar(select(Recruiter).where(Recruiter.display_name == "管理员"))
    second_admin = Recruiter(
        company_id=admin.company_id,
        display_name="李先生",
        feishu_display_name="李启明",
        email="li@example.com",
        role="ADMIN",
        password_hash=hash_password("dev-password"),
    )
    session.add(second_admin)
    session.commit()
    source_id = conversation_sync(client, login(client, "xie@example.com"), context("重置候选人", "谢女士", "reset-source")).json()["candidate_source_id"]
    preview = client.get("/api/v1/admin/system-reset/preview", headers=login(client, "admin@example.com"))
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert {identity["display_name"] for identity in data["keep_identities"]} == {"管理员", "李先生"}
    response = client.post(
        "/api/v1/admin/system-reset",
        headers=login(client, "admin@example.com"),
        json={"preview_version": data["preview_version"], "confirmation": "重新初始化同步"},
    )
    assert response.status_code == 200, response.text
    assert session.get(CandidateSource, source_id) is None
    # Administrators are not a fixed identity: every administrator survives,
    # including the one who ran the reset; ordinary recruiters are removed.
    assert {recruiter.display_name for recruiter in session.scalars(select(Recruiter)).all()} == {"管理员", "李先生"}
    assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_recruiter_admin_lists_are_scoped_to_their_own_account(client):
    assert conversation_sync(client, login(client, "xie@example.com"), context("本人候选人", "谢女士", "scope-xie")).status_code == 200
    assert conversation_sync(client, login(client, "jiali@example.com"), context("他人候选人", "珈莉", "scope-jiali")).status_code == 200
    response = client.get("/api/v1/admin/candidate-sources", headers=login(client, "xie@example.com"))
    assert response.status_code == 200
    assert [row["candidate_display_name"] for row in response.json()] == ["本人候选人"]
    assert [row["display_name"] for row in client.get("/api/v1/admin/recruiters", headers=login(client, "xie@example.com")).json()] == ["谢女士"]


def test_click_evidence_upgrade_bypasses_cooldown(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    first_payload = context("升级证据候选人", "谢女士", "upgrade-first")
    source_id = conversation_sync(client, xie, first_payload).json()["candidate_source_id"]
    current_payload = context("升级证据候选人", "珈莉", "upgrade-second")
    current_payload["candidate_age"] = 31
    suspected = client.post("/api/v1/plugin/context/check", headers=jiali, json=current_payload)
    assert suspected.json()["result_type"] == "CHECK_ONLY_NO_HISTORY"
    assert suspected.json()["lookup_notifications_queued"] == 0
    source = session.get(CandidateSource, source_id)
    source.candidate_age = 31
    source.candidate_identity_signature = candidate_identity_signature("升级证据候选人", 31, "6年", "本科")
    session.commit()
    upgraded = client.post("/api/v1/plugin/context/check", headers=jiali, json={**current_payload, "client_event_id": str(uuid.uuid4())})
    assert upgraded.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert upgraded.json()["lookup_notifications_queued"] == 1
    alert = session.scalar(select(DuplicateLookupAlert))
    assert alert.evidence_rank == 2 and alert.notification_version == 1
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1


def test_boss_native_history_warns_recruiter_with_latest_activity(client, session):
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
    assert response.json()["lookup_notifications_queued"] == 1
    assert session.scalar(select(DuplicateLookupAlert)).evidence_rank == 3
    latest = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    queued = session.scalars(select(NotificationOutbox)).all()
    assert [row.recipient_recruiter_id for row in queued] == [latest.id]
    repeat = client.post("/api/v1/plugin/context/check", headers=headers, json={**payload, "client_event_id": str(uuid.uuid4())})
    assert repeat.status_code == 200
    assert repeat.json()["lookup_notifications_queued"] == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1


def test_native_history_treats_boss_nickname_and_bound_feishu_name_as_self(session):
    service = RecruitmentCollaborationService(session)
    matches = service._merge_native_matches(
        [{"recruiter_name": "李启明", "job_name": "ai应用开发工程师", "contacted_at": "2026-09-15T13:04:00+08:00"}],
        {"李先生", "李启明"},
        {"ai应用开发工程师"},
        [],
        True,
    )
    assert matches == []


def test_duplicate_lookup_notifies_only_recruiter_with_latest_activity(session):
    viewer = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))
    colleague = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    viewer.feishu_open_id = "open-viewer"
    colleague.feishu_open_id = "open-colleague"
    session.commit()
    payload = context("最新沟通候选人", "珈莉", "latest-owner")
    payload["conversation_updated_at"] = "2026-09-15T13:04:00+08:00"
    match = {
        "match_level": "EXACT_IDENTITY",
        "candidate_source_id": "previous-row",
        "recruiter_id": colleague.id,
        "recruiter_name": colleague.display_name,
        "job_id": None,
        "job_name": "另一个岗位",
        "stage": "沟通中",
        "last_activity_at": "2026-09-15T12:00:00+08:00",
        "match_reason": "姓名、年龄、工作年限、学历一致；招聘者和岗位不同",
    }
    assert RecruitmentCollaborationService(session)._queue_lookup_alerts(viewer.company_id, viewer, payload, [match]) == 1
    session.commit()
    recipients = set(session.scalars(select(NotificationOutbox.recipient_recruiter_id)).all())
    assert recipients == {viewer.id}


def test_duplicate_lookup_targets_previous_recruiter_when_their_activity_is_newer(session):
    viewer = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))
    colleague = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    viewer.feishu_open_id = "open-viewer"
    colleague.feishu_open_id = "open-colleague"
    session.commit()
    payload = context("此前招聘者更新候选人", "珈莉", "previous-latest")
    payload["conversation_updated_at"] = "2026-09-15T12:00:00+08:00"
    match = {
        "match_level": "EXACT_IDENTITY",
        "candidate_source_id": "newer-previous-row",
        "recruiter_id": colleague.id,
        "recruiter_name": colleague.display_name,
        "job_id": None,
        "job_name": "另一个岗位",
        "stage": "沟通中",
        "last_activity_at": "2026-09-15T13:04:00+08:00",
        "match_reason": "姓名、年龄、工作年限、学历一致；招聘者和岗位不同",
    }
    assert RecruitmentCollaborationService(session)._queue_lookup_alerts(viewer.company_id, viewer, payload, [match]) == 1
    session.commit()
    recipients = set(session.scalars(select(NotificationOutbox.recipient_recruiter_id)).all())
    assert recipients == {colleague.id}


def test_a_newly_synced_row_still_notifies_both_recruiters_about_the_conflict(client, session):
    """Only the browse warning is viewer-only; a genuine conflict stays bilateral.

    Rows are created by the sync paths, so the persistent conflict and its
    bilateral notifications now fire when the second row is synced.
    """
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    assert conversation_sync(client, xie, context("冲突提醒候选人", "谢女士", "conflict-first")).status_code == 200
    assert conversation_sync(client, jiali, context("冲突提醒候选人", "珈莉", "conflict-second")).status_code == 200
    conflict = session.scalar(select(Conflict))
    assert conflict is not None
    recipients = set(
        session.scalars(
            select(NotificationOutbox.recipient_recruiter_id).where(
                NotificationOutbox.event_type == "CONFLICT_CREATED"
            )
        ).all()
    )
    assert len(recipients) == 2


def test_same_recruiter_same_name_and_job_with_different_age_stays_separate(client, session):
    first = context("同名候选人", "谢女士", "first")
    second = context("同名候选人", "谢女士", "second")
    second["candidate_age"] = 31
    assert conversation_sync(client, {}, first).status_code == 200
    # A genuinely different person may share the name and job; a later
    # observation of them still creates their own row.
    assert conversation_sync(client, {}, second, "2026-09-02T02:00:00Z").status_code == 200
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2


def test_snapshot_upload_keeps_only_tokens_and_requeues_candidate(client, session):
    source_id = conversation_sync(client, {}, context("快照候选人", "谢女士", "snapshot")).json()["candidate_source_id"]
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
    source_id = conversation_sync(client, login(client, "xie@example.com"), context("归属候选人", "谢女士", "owner")).json()["candidate_source_id"]
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
    source_id = conversation_sync(client, {}, context("简历候选人", "谢女士", "resume")).json()["candidate_source_id"]
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
    source_id = conversation_sync(client, {}, context(f"格式候选人-{file_name}", "谢女士", file_name)).json()["candidate_source_id"]
    response = client.post(
        f"/api/v1/plugin/conversations/{source_id}/resume",
        data={"resume_hash": hashlib.sha256(content).hexdigest(), "related_source_ids": ""},
        files={"file": (file_name, content, content_type)},
    )
    assert response.status_code == 200, response.text
    assert session.get(CandidateSource, source_id).resume_status == "READY"


def test_scan_report_records_monitoring_time_without_anchoring_candidates(client, session):
    """Polling is anchor-free; the report is monitoring data, never a filter."""
    from recruitment_collab.infrastructure.models import ConversationScanCheckpoint

    headers = login(client, "xie@example.com")
    response = client.post(
        "/api/v1/plugin/conversations/scan-report",
        headers=headers,
        json={
            "platform": "boss",
            "account_display_name": "谢女士",
            "completed_through_at": datetime.now(timezone.utc).isoformat(),
            "cursor": {"traversed": 12, "candidates_opened": 3, "unread_left_alone": 9},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True
    row = session.scalar(
        select(ConversationScanCheckpoint).where(ConversationScanCheckpoint.account_display_name == "谢女士")
    )
    assert row is not None
    assert row.cursor_json["unread_left_alone"] == 9


def test_scan_report_rejects_a_future_pass_time(client):
    headers = login(client, "xie@example.com")
    response = client.post(
        "/api/v1/plugin/conversations/scan-report",
        headers=headers,
        json={
            "platform": "boss",
            "account_display_name": "谢女士",
            "completed_through_at": "2099-01-01T00:00:00Z",
            "cursor": {},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CHECKPOINT_TIME_INVALID"


def test_unmapped_recruitment_account_does_not_block_candidate_flow(client):
    headers = login(client, "xie@example.com")
    response = conversation_sync(client, headers, context("小明", "无需绑定的页面账号", "unmapped-account"))
    assert response.status_code == 200
    data = response.json()
    assert data["result_type"] == "NO_HISTORY"
    assert data["account_mapping"]["status"] == "MAPPED"
    assert data["feishu_candidate_fields"]["BOSS账号"] == "无需绑定的页面账号"
    assert data["feishu_candidate_fields"]["飞书账号"] == "无需绑定的页面账号"
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
    response = conversation_sync(client, headers, payload)
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_source_id"]
    assert data["job_mapping"]["status"] == "RAW"
    assert data["job_mapping"]["raw_name"] == "全新 BOSS 岗位"
    assert data["job_mapping"]["normalization"] == "OPTIONAL"
    assert data["feishu_candidate_fields"]["BOSS岗位"] == "全新 BOSS 岗位"
    assert data["feishu_candidate_fields"]["标准岗位"] == ""
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    # The sync paths own row creation and do not log MESSAGE_SENT events.
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_mapping_an_unmapped_job_backfills_existing_business_rows(client, session):
    payload = context("回填岗位候选人", "谢女士", "backfill")
    payload["job_display_name"] = "待回填岗位"
    source_id = conversation_sync(client, login(client, "xie@example.com"), payload).json()["candidate_source_id"]
    unmapped = session.scalar(select(UnmappedJob).where(UnmappedJob.normalized_job_name == "待回填岗位"))
    job = session.scalar(select(RecruitmentJob))
    response = client.patch(f"/api/v1/admin/unmapped-jobs/{unmapped.id}", headers=login(client, "admin@example.com"), json={"job_id": job.id})
    assert response.status_code == 200
    assert response.json()["backfilled_candidate_count"] == 1
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.job_id == job.id
    assert session.scalar(select(Engagement).where(Engagement.candidate_source_id == source_id)) is None
    assert session.scalar(select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id)) is None
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
    first_id = conversation_sync(client, headers, first).json()["candidate_source_id"]
    second_id = conversation_sync(client, headers, second).json()["candidate_source_id"]
    assert first_id != second_id
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 2


def test_status_only_advances_on_stronger_explicit_evidence(client, session):
    headers = login(client, "xie@example.com")
    payload = context("状态候选人", "谢女士", "status")
    first = conversation_sync(client, headers, {**payload, "recruitment_status": "已约面", "status_evidence": "BOSS_INTERVIEW_MARKER"})
    assert first.status_code == 200
    payload["client_event_id"] = str(uuid.uuid4())
    lower = conversation_sync(client, headers, {**payload, "recruitment_status": "已获取简历", "status_evidence": "BOSS_RESUME_MARKER"}, "2026-09-02T02:00:00Z")
    assert lower.status_code == 200
    source = session.get(CandidateSource, first.json()["candidate_source_id"])
    assert source.recruitment_status == "已约面"
    payload["client_event_id"] = str(uuid.uuid4())
    terminal = conversation_sync(client, headers, {**payload, "recruitment_status": "已拒绝", "status_evidence": "EXPLICIT_REJECTION"}, "2026-09-02T03:00:00Z")
    assert terminal.status_code == 200
    payload["client_event_id"] = str(uuid.uuid4())
    assert conversation_sync(client, headers, {**payload, "recruitment_status": "沟通中", "status_evidence": "RECRUITER_OUTBOUND"}, "2026-09-02T04:00:00Z").status_code == 200
    assert session.get(CandidateSource, first.json()["candidate_source_id"]).recruitment_status == "已拒绝"


def test_newer_recontact_intent_reopens_existing_candidate(client, session):
    headers = login(client, "xie@example.com")
    payload = context("重新沟通候选人", "谢女士", "recontact")
    first = conversation_sync(client, headers, {**payload, "recruitment_status": "已拒绝", "status_evidence": "EXPLICIT_REJECTION"})
    assert first.status_code == 200
    second = conversation_sync(client, headers, {
        **payload, "client_event_id": str(uuid.uuid4()),
        "recruitment_status": "待约面", "status_evidence": "RECRUITER_RECONTACT_INTENT",
    }, "2026-09-07T01:00:00Z")
    assert second.status_code == 200
    assert second.json()["candidate_source_id"] == first.json()["candidate_source_id"]
    source = session.get(CandidateSource, first.json()["candidate_source_id"])
    assert source.recruitment_status == "待约面"
    assert source.conversation_updated_at.day == 7


def test_v3_neutral_evidence_repairs_a_legacy_false_rejection(client, session):
    headers = login(client, "xie@example.com")
    payload = context("旧误拒绝候选人", "谢女士", "legacy-rejection")
    first = conversation_sync(
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
    repaired = conversation_sync(
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
    source_id = conversation_sync(client, {}, context("快照状态候选人", "谢女士", "snapshot-status")).json()["candidate_source_id"]
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
    first = conversation_sync(client, headers, payload).json()
    payload["client_event_id"] = str(uuid.uuid4())
    second = conversation_sync(client, headers, payload, "2026-09-02T02:00:00Z").json()
    assert second["candidate_source_id"] == first["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert second["feishu_candidate_fields"]["BOSS账号"] == "李先生"
    queued = session.scalar(select(CandidateSyncOutbox))
    assert queued is not None
    assert queued.payload_version == 2
    assert queued.payload_json["更新时间"] == 1788314400000


def test_plugin_context_does_not_require_internal_login_token(client, session):
    payload = context("无令牌候选人", "李先生", "no-token")
    checked = client.post("/api/v1/plugin/context/check", json=payload)
    assert checked.status_code == 200
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 0
    sent = conversation_sync(client, {}, payload)
    assert sent.status_code == 200
    assert sent.json()["feishu_candidate_fields"]["BOSS账号"] == "李先生"
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_production_plugin_endpoints_require_an_active_feishu_device(client, session, monkeypatch):
    from recruitment_collab.api import dependencies, routes

    production = get_settings().model_copy(update={"app_env": "production", "feishu_mode": "mock"})
    monkeypatch.setattr(dependencies, "get_settings", lambda: production)
    monkeypatch.setattr(routes, "get_settings", lambda: production)
    payload = context("设备候选人", "谢女士", "device-auth")
    assert client.post("/api/v1/plugin/context/check", json=payload).status_code == 401
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
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
    session.add(BossAccountAssignment(company_id=recruiter.company_id, boss_account_id=account.id, feishu_recruiter_id=recruiter.id, feishu_display_name=recruiter.display_name))
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
    # Rows are owned by the sync paths; a send attaches its audit event to an
    # existing row and a retried send stays idempotent.
    source_id = conversation_sync(client, headers, payload).json()["candidate_source_id"]
    first = message_sent(client, headers, payload)
    repeat = message_sent(client, headers, payload)
    assert first.status_code == repeat.status_code == 200
    assert repeat.json()["idempotent"] is True
    assert repeat.json()["candidate_source_id"] == source_id
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    assert session.scalar(select(func.count()).select_from(RecruitmentEvent)) == 1
    assert session.scalar(select(func.count()).select_from(CandidateSyncOutbox)) == 1


def test_snapshot_is_requested_only_by_the_history_sync(client, session):
    headers = login(client, "xie@example.com")
    opened = {
        **context("邀约截图候选人", "谢女士", "invite-snapshot"),
        "sent_at": "2026-09-02T01:00:00Z",
        "has_recruiter_outbound": False,
        "sync_reason": "CANDIDATE_OPENED",
    }
    first = client.post("/api/v1/plugin/conversations/sync", json=opened)
    assert first.status_code == 200
    # Only the history sweep captures: a click, a reconcile pass or a plain
    # re-open never photographs the recruiter's own pane.
    assert first.json()["snapshot_needed"] is False
    source = session.get(CandidateSource, first.json()["candidate_source_id"])
    source.snapshot_status, source.snapshot_tokens_json = "READY", ["snapshot-token"]
    session.commit()
    newer = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **opened,
            "client_event_id": str(uuid.uuid4()),
            "sent_at": "2026-09-02T02:00:00Z",
            "conversation_updated_at": "2026-09-02T02:00:00Z",
        },
    )
    assert newer.status_code == 200
    assert newer.json()["snapshot_needed"] is False

    history = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **opened,
            "client_event_id": str(uuid.uuid4()),
            "sent_at": "2026-09-02T03:00:00Z",
            "conversation_updated_at": "2026-09-02T03:00:00Z",
            "sync_reason": "HISTORY_SNAPSHOT",
        },
    )
    assert history.status_code == 200
    assert history.json()["snapshot_needed"] is True

    # An ordinary message only registers the event: the full chat capture
    # scrolls the pane the recruiter is still typing in.
    chat = message_sent(
        client,
        headers,
        {
            **context("邀约截图候选人", "谢女士", "invite-snapshot"),
            "status_evidence": "RECRUITER_OUTBOUND",
            "status_rule_version": "boss-status-v4",
        },
    )
    assert chat.status_code == 200
    assert chat.json()["snapshot_needed"] is False
    # A send attaches to the synced row without creating a second one.
    assert chat.json()["candidate_source_id"] == first.json()["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1


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
    first = conversation_sync(client, headers, payload, "2026-09-02T01:00:00Z")
    assert first.status_code == 200
    payload.update({"client_event_id": str(uuid.uuid4())})
    second = conversation_sync(client, headers, payload, "2026-09-01T01:00:00Z")
    assert second.status_code == 200
    payload.update({"client_event_id": str(uuid.uuid4())})
    third = conversation_sync(client, headers, payload, "2026-09-03T02:00:00Z")
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
    first = conversation_sync(client, xie, context("小明", "谢女士", "xie")).json()
    check = client.post("/api/v1/plugin/context/check", headers=jiali, json=context("小明", "珈莉", "jiali")).json()
    assert check["result_type"] == "CONFIRMED_DUPLICATE"
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    # The row — and with it the persistent conflict — is created by the sync
    # path, not by a send.
    second = conversation_sync(client, jiali, context("小明", "珈莉", "jiali")).json()
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
    source = conversation_sync(client, headers, context("张女士", "谢女士", "reason")).json()
    response = client.post(
        "/api/v1/plugin/events",
        headers=headers,
        json={"candidate_source_id": source["candidate_source_id"], "event_type": "CONTINUED_AFTER_WARNING", "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REASON_REQUIRED"


def _reconcile(client, headers, name, job, activity, date_only=False):
    return client.post(
        "/api/v1/plugin/conversations/reconcile",
        headers=headers,
        json={
            "platform": "mock",
            "account_display_name": "谢女士",
            "candidate_display_name": name,
            "job_display_name": job,
            "list_activity_at": activity,
            "list_activity_date_only": date_only,
        },
    )


def test_same_candidate_merges_when_one_profile_card_is_incomplete(client, session):
    """One candidate + one BOSS account + one job is exactly one row.

    BOSS hydrates the profile card in stages, so a second observation of the
    same person can carry a different (or missing) age/education and therefore
    a different identity signature. It must reuse the existing row.
    """
    headers = login(client, "xie@example.com")
    first = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("补全资料候选人", "谢女士", "merge-one"),
            "sent_at": "2025-09-02T01:00:00Z",
            "conversation_updated_at": "2025-09-02T01:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    second = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("补全资料候选人", "谢女士", "merge-two"),
            "candidate_age": None,
            "candidate_experience": None,
            "candidate_education": None,
            "sent_at": "2025-09-02T02:00:00Z",
            "conversation_updated_at": "2025-09-02T02:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    assert second["candidate_source_id"] == first["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1
    source = session.get(CandidateSource, first["candidate_source_id"])
    assert source.candidate_age == 28
    assert source.candidate_education == "本科"


def test_same_age_with_a_shifted_degree_line_is_still_one_candidate(client, session):
    """Education/experience drift between scans must not split one person."""
    headers = login(client, "xie@example.com")
    base = {
        **context("学历抖动候选人", "谢女士", "drift"),
        "sent_at": "2025-09-02T01:00:00Z",
        "conversation_updated_at": "2025-09-02T01:00:00Z",
        "has_recruiter_outbound": False,
        "sync_reason": "UNREAD_CANDIDATE_OPENED",
    }
    first = client.post("/api/v1/plugin/conversations/sync", json=base).json()
    second = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **base,
            "client_event_id": str(uuid.uuid4()),
            "candidate_experience": "26年",
            "candidate_education": "大专",
            "conversation_updated_at": "2025-09-02T03:00:00Z",
        },
    ).json()
    assert second["candidate_source_id"] == first["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1


def test_leaked_list_metadata_is_not_part_of_the_job_identity(client, session):
    """A leaked list row in the job field must not create a second candidate."""
    headers = login(client, "xie@example.com")
    clean = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("岗位噪声候选人", "谢女士", "job-clean"),
            "sent_at": "2025-09-02T01:00:00Z",
            "conversation_updated_at": "2025-09-02T01:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    noisy = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("岗位噪声候选人", "谢女士", "job-noisy"),
            "job_display_name": "短视频编导 最近关注: 无锡 · 主播 6-11K 17:58 9月11日 沟通的职位-短视频编导 送达 你好",
            "sent_at": "2025-09-02T02:00:00Z",
            "conversation_updated_at": "2025-09-02T02:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    assert noisy["candidate_source_id"] == clean["candidate_source_id"]
    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1


def test_reconcile_prices_each_list_row_against_the_table(client, session):
    """The reader's rule: absent -> create, newer -> update, same -> skip."""
    headers = login(client, "xie@example.com")
    created = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("对账候选人", "谢女士", "reconcile"),
            "sent_at": "2025-09-02T02:00:00Z",
            "conversation_updated_at": "2025-09-02T02:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    source_id = created["candidate_source_id"]

    missing = _reconcile(client, headers, "没见过的候选人", "短视频编导", "2025-09-02T02:00:00Z")
    assert missing.status_code == 200, missing.text
    assert missing.json()["decision"] == "SYNC"
    assert missing.json()["reason"] == "CANDIDATE_NOT_IN_TABLE"

    stored = session.get(CandidateSource, source_id)
    stored.feishu_record_id = None
    session.commit()
    unsent = _reconcile(client, headers, "对账候选人", "短视频编导", "2025-09-02T02:00:00Z")
    assert unsent.json()["decision"] == "SYNC"
    assert unsent.json()["reason"] == "FEISHU_ROW_MISSING"

    stored.feishu_record_id = "rec-existing"
    session.commit()
    same = _reconcile(client, headers, "对账候选人", "短视频编导", "2025-09-02T02:00:00Z")
    assert same.json()["decision"] == "SKIP"
    assert same.json()["reason"] == "LIST_ACTIVITY_UNCHANGED"

    older = _reconcile(client, headers, "对账候选人", "短视频编导", "2025-09-02T01:00:00Z")
    assert older.json()["decision"] == "SKIP"

    newer = _reconcile(client, headers, "对账候选人", "短视频编导", "2025-09-02T03:00:00Z")
    assert newer.json()["decision"] == "SYNC"
    assert newer.json()["reason"] == "LIST_ACTIVITY_NEWER"


def test_reconcile_compares_calendar_only_rows_by_day(client, session):
    """`09月02日` carries no clock, so it must never be read as midnight."""
    headers = login(client, "xie@example.com")
    created = client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("日历候选人", "谢女士", "date-only"),
            "sent_at": "2025-09-02T18:30:00Z",
            "conversation_updated_at": "2025-09-02T18:30:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    ).json()
    stored = session.get(CandidateSource, created["candidate_source_id"])
    stored.feishu_record_id = "rec-calendar"
    session.commit()

    same_day = _reconcile(client, headers, "日历候选人", "短视频编导", "2025-09-02T00:00:00Z", date_only=True)
    assert same_day.json()["decision"] == "SKIP"
    later_day = _reconcile(client, headers, "日历候选人", "短视频编导", "2025-09-03T00:00:00Z", date_only=True)
    assert later_day.json()["decision"] == "SYNC"
    assert later_day.json()["reason"] == "LIST_ACTIVITY_NEWER"


def test_message_bubble_time_is_not_rolled_back_by_a_later_scan(client, session):
    """更新时间 follows the sent bubble, not the list row."""
    headers = login(client, "xie@example.com")
    source_id = conversation_sync(
        client, headers, context("气泡时间候选人", "谢女士", "bubble"),
        sent_at="2025-09-02T05:30:00Z",
    ).json()["candidate_source_id"]
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.conversation_updated_at.isoformat().startswith("2025-09-02T05:30:00")

    client.post(
        "/api/v1/plugin/conversations/sync",
        json={
            **context("气泡时间候选人", "谢女士", "bubble-again"),
            "sent_at": "2025-09-02T09:10:00Z",
            "conversation_updated_at": "2025-09-02T09:10:00Z",
            "has_recruiter_outbound": True,
            "sync_reason": "UNREAD_CANDIDATE_OPENED",
        },
    )
    session.expire_all()
    source = session.get(CandidateSource, source_id)
    assert source.conversation_updated_at.isoformat().startswith("2025-09-02T09:10:00")


def test_browse_alert_warns_latest_recruiter_and_reconciliation_is_silent(client, session):
    """Where the “候选人浏览查重提醒” comes from, and who it reaches."""
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    xie_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    jiali_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))
    assert conversation_sync(client, xie, context("浏览提醒候选人", "谢女士", "browse-first")).status_code == 200

    payload = context("浏览提醒候选人", "珈莉", "browse-second")
    opened = client.post("/api/v1/plugin/context/check", headers=jiali, json=payload)
    assert opened.status_code == 200, opened.text
    assert opened.json()["result_type"] == "CONFIRMED_DUPLICATE"
    assert opened.json()["lookup_notifications_queued"] == 1
    alert = session.scalar(select(DuplicateLookupAlert))
    assert alert is not None and alert.matched_recruiter_id == xie_recruiter.id
    recipients = set(session.scalars(select(NotificationOutbox.recipient_recruiter_id)).all())
    assert recipients == {xie_recruiter.id}
    assert jiali_recruiter.id not in recipients

    reconciled = client.post(
        "/api/v1/plugin/conversations/sync",
        headers=jiali,
        json={
            **payload,
            "client_event_id": str(uuid.uuid4()),
            "sent_at": "2025-09-02T03:00:00Z",
            "conversation_updated_at": "2025-09-02T03:00:00Z",
            "has_recruiter_outbound": False,
            "sync_reason": "CATCHUP_RECONCILED",
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1


def test_several_matching_colleagues_are_merged_into_one_card(session):
    """The same person held by two colleagues is still one message."""
    viewer = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))
    viewer.feishu_open_id = "open-viewer"
    colleague_a = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    colleague_a.feishu_open_id = "open-a"
    colleague_b = Recruiter(
        company_id=viewer.company_id,
        display_name="王女士",
        email="wang-second@example.com",
        role="RECRUITER",
        password_hash=colleague_a.password_hash,
        feishu_open_id="open-b",
    )
    session.add(colleague_b)
    session.commit()

    base_match = {
        "match_level": "EXACT_IDENTITY",
        "candidate_source_id": "rec-row",
        "recruiter_id": None,
        "job_id": None,
        "job_name": "短视频编导",
        "stage": "沟通中",
        "match_reason": "姓名、年龄、工作年限、学历完全一致",
    }
    queued = RecruitmentCollaborationService(session)._queue_lookup_alerts(
        viewer.company_id,
        viewer,
        context("多人命中候选人", "珈莉", "multi-merged"),
        [
            {
                **base_match,
                "recruiter_name": "谢女士",
                "first_contact_at": datetime(2026, 8, 25, 18, 29, tzinfo=timezone.utc).timestamp() * 1000,
            },
            {
                **base_match,
                "recruiter_name": "王女士",
                "first_contact_at": datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc).timestamp() * 1000,
            },
        ],
    )
    session.commit()

    # Two colleagues: two alert rows (cooldown + audit each), one card.
    assert session.scalar(select(func.count()).select_from(DuplicateLookupAlert)) == 2
    assert queued == 1
    cards = session.scalars(select(NotificationOutbox)).all()
    assert len(cards) == 1
    payload = cards[0].payload_json
    assert payload["matched_colleague_count"] == 2
    # The recipient is the recruiter with the newest real contact (王女士); the
    # card describes the *other* side and therefore never names its recipient.
    recipient = session.get(Recruiter, cards[0].recipient_recruiter_id)
    assert recipient.display_name == "王女士"
    names = {item["recruiter_name"] for item in payload["matched_recruiter_details"]}
    assert names == {"珈莉", "谢女士"}
    assert recipient.display_name not in names
    for alert in session.scalars(select(DuplicateLookupAlert)).all():
        assert alert.last_notified_at is not None


def test_second_recruiter_who_really_comms_receives_the_lookup_card(client, session):
    """A confirmed send is real communication and decides who is reminded.

    The first recruiter contacts the candidate alone and hears nothing. When a
    second recruiter actually writes to the same candidate, that newer real
    conversation — not the first recruiter's stored row time — decides who gets
    the card.
    """
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    xie_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    jiali_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "珈莉"))

    # The first recruiter's row comes from the sync path; only the second
    # recruiter's actual send is a send.
    first = conversation_sync(client, xie, context("先后沟通候选人", "谢女士", "two-first"), sent_at="2026-09-15T10:00:00+08:00")
    assert first.status_code == 200, first.text
    # Nobody else held the candidate yet, so the first communication is silent.
    assert session.scalar(select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.event_type == "DUPLICATE_LOOKUP")) == 0

    second = message_sent(client, jiali, context("先后沟通候选人", "珈莉", "two-second"), sent_at="2026-09-15T11:00:00+08:00")
    assert second.status_code == 200, second.text
    assert second.json()["lookup_notifications_queued"] == 1

    cards = session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "DUPLICATE_LOOKUP")).all()
    assert [card.recipient_recruiter_id for card in cards] == [jiali_recruiter.id]
    assert xie_recruiter.id not in {card.recipient_recruiter_id for card in cards}
    payload = cards[0].payload_json
    assert payload["trigger"] == "SEND"
    assert payload["recipient_is_viewer"] is True
    # The card tells the second communicator about the colleague who already has
    # the candidate, never about themselves.
    assert payload["matched_recruiter_name"] == "谢女士"
    assert payload["matched_recruiter_names"] == ["谢女士"]


def test_lookup_card_never_names_its_own_recipient(client, session):
    """A browse by a second recruiter warns the newest real communicator."""
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    for recruiter in session.scalars(select(Recruiter).where(Recruiter.display_name.in_({"谢女士", "珈莉"}))).all():
        recruiter.feishu_open_id = f"open-{recruiter.id}"
    session.commit()
    xie_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert conversation_sync(client, xie, context("只浏览候选人", "谢女士", "browse-first"), sent_at="2026-09-15T10:00:00+08:00").status_code == 200

    opened = client.post("/api/v1/plugin/context/check", headers=jiali, json=context("只浏览候选人", "珈莉", "browse-second"))
    assert opened.status_code == 200, opened.text
    cards = session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "DUPLICATE_LOOKUP")).all()
    assert [card.recipient_recruiter_id for card in cards] == [xie_recruiter.id]
    payload = cards[0].payload_json
    assert payload["trigger"] == "BROWSE"
    assert payload["recipient_is_viewer"] is False
    assert payload["matched_recruiter_name"] == "珈莉"
    assert "谢女士" not in payload["matched_recruiter_names"]


def test_own_cross_job_record_is_flagged_as_own_history(client, session):
    """A cross-job record of the viewer's own stays visible, but is worded as
    their own history with the other job named — never as a colleague
    conflict naming the viewer.
    """
    xie = login(client, "xie@example.com")
    xie_recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    first = conversation_sync(client, xie, context("重复跟进候选人", "谢女士", "own-job-one"), sent_at="2026-09-15T10:00:00+08:00")
    assert first.status_code == 200, first.text
    second_payload = context("重复跟进候选人", "谢女士", "own-job-two")
    second_payload["job_display_name"] = "实习剪辑"
    second = message_sent(client, xie, second_payload, sent_at="2026-09-16T10:30:00+08:00")
    assert second.status_code == 200, second.text
    body = second.json()
    own = [item for item in body["matches"] if item.get("is_own_history")]
    assert own and own[0]["recruiter_id"] == xie_recruiter.id
    assert own[0]["job_name"] == "短视频编导"
    assert body["ui"]["title"] == "你本人跟进过的候选人"
    assert "你本人" in body["ui"]["message"]
    assert "实习剪辑" not in body["ui"]["message"] or "短视频编导" in body["ui"]["message"]
    assert f"{xie_recruiter.display_name} 已在跟进该候选人" not in body["ui"]["message"]


def test_chat_text_glued_to_the_job_label_keeps_one_row(client, session):
    """BOSS renders the first chat line in the same text run as the job title.

    The polluted title used to be part of the candidate's identity key and of
    the same-job comparison, so one candidate whose conversation kept moving
    gained one row per distinct chat text (张雨庭 ×4).
    """
    headers = login(client, "xie@example.com")
    listed = context("张雨庭", "谢女士", "job-glue-first")
    listed["job_display_name"] = "AI短视频内容生成师"
    source_id = conversation_sync(client, headers, listed).json()["candidate_source_id"]

    for suffix, glue in (
        ("job-glue-second", " BOSS您好,我具备岗位所需技能,且学习能力强,可以给您发简历看看吗? 10:33"),
        ("job-glue-third", " BOSS您好,我具备岗位所需技能,且学习能力强,可以给您发简历看看吗? 你好啊,可以聊一聊~ 不好意思,不太合适哦 求简历 换电话 换"),
    ):
        polluted = context("张雨庭", "谢女士", suffix)
        polluted["job_display_name"] = f"AI短视频内容生成师{glue}"
        again = conversation_sync(client, headers, polluted, "2026-09-02T02:00:00Z")
        assert again.status_code == 200, again.text
        assert again.json()["candidate_source_id"] == source_id

    assert session.scalar(select(func.count()).select_from(CandidateSource)) == 1


def test_job_label_canonicalization_strips_page_and_chat_tails():
    from recruitment_collab.application.collaboration import _canonical_job_name

    assert _canonical_job_name("AI短视频内容生成师") == "AI短视频内容生成师"
    assert _canonical_job_name("AI短视频内容生成师 BOSS您好,我具备岗位所需技能") == "AI短视频内容生成师"
    assert _canonical_job_name("直播助播 您好,我想和您沟通下…") == "直播助播"
    assert _canonical_job_name("财务主管 最近关注：无锡") == "财务主管"
    assert _canonical_job_name("AI短视频内容生成师 10:33") == "AI短视频内容生成师"
    # A title that contains nothing but the tail is kept rather than emptied.
    assert _canonical_job_name("您好") == "您好"


def test_plugin_me_reports_the_assigned_boss_account(client, session):
    """The header-less fallback reads this name, so it must come from the
    device's assignment rather than from a retyped string."""
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    account = session.scalar(
        select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士")
    )
    headers = login(client, "xie@example.com")
    assert client.get("/api/v1/plugin/me", headers=headers).json()["boss_account_name"] == ""

    session.add(
        BossAccountAssignment(
            company_id=recruiter.company_id,
            boss_account_id=account.id,
            feishu_recruiter_id=recruiter.id,
            feishu_display_name=recruiter.display_name,
        )
    )
    session.commit()
    assert client.get("/api/v1/plugin/me", headers=headers).json()["boss_account_name"] == "谢女士"
