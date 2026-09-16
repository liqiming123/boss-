from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.models import CandidateSource, CandidateSyncOutbox, now
from recruitment_collab.workers import data_retention_worker


def test_synced_candidate_cache_is_minimized_but_operational_keys_remain(client, session, monkeypatch):
    from test_api_flow import context, conversation_sync

    source_id = conversation_sync(client, {}, context("待最小化候选人", "谢女士", "retention")).json()["candidate_source_id"]
    source = session.get(CandidateSource, source_id)
    source.feishu_record_id = "rec-feishu"
    source.last_seen_at = now() - timedelta(days=31)
    signature, conversation_key = source.candidate_identity_signature, source.conversation_job_key
    session.commit()
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(data_retention_worker, "SessionLocal", factory)
    monkeypatch.setattr(data_retention_worker, "get_settings", lambda: get_settings().model_copy(update={"candidate_cache_days": 30}))
    result = data_retention_worker.process_retention()
    session.expire_all()
    minimized = session.scalar(select(CandidateSource).where(CandidateSource.id == source_id))
    assert result["candidates_minimized"] == 1
    assert minimized is not None
    assert minimized.candidate_display_name == "已同步至飞书"
    assert minimized.candidate_age is None and minimized.candidate_experience is None and minimized.candidate_education is None
    assert minimized.candidate_identity_signature == signature
    assert minimized.conversation_job_key == conversation_key
    assert minimized.feishu_record_id == "rec-feishu"
    assert minimized.data_minimized_at is not None


def test_stale_unsynced_candidate_is_minimized_after_sync_reaches_terminal_state(client, session, monkeypatch):
    from test_api_flow import context, conversation_sync

    source_id = conversation_sync(client, {}, context("未同步但已失败候选人", "谢女士", "unsynced-retention")).json()["candidate_source_id"]
    source = session.get(CandidateSource, source_id)
    source.last_seen_at = now() - timedelta(days=31)
    outbox = session.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.candidate_source_id == source_id))
    outbox.status = "FAILED"
    outbox.updated_at = now()
    session.commit()
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(data_retention_worker, "SessionLocal", factory)
    monkeypatch.setattr(
        data_retention_worker,
        "get_settings",
        lambda: get_settings().model_copy(update={"candidate_cache_days": 30, "failed_task_retention_days": 60}),
    )
    result = data_retention_worker.process_retention()
    session.expire_all()
    minimized = session.get(CandidateSource, source_id)
    outbox = session.get(CandidateSyncOutbox, outbox.id)
    assert result["candidates_minimized"] == 1
    assert minimized is not None
    assert minimized.candidate_display_name == "已按保留期限最小化"
    assert minimized.feishu_record_id is None
    assert minimized.candidate_age is None
    assert minimized.data_minimized_at is not None
    # The failed payload remains available during the independent retry
    # retention window, so an administrator can still retry the terminal task.
    assert outbox.payload_json


def test_active_unsynced_candidate_is_kept_for_worker_retry(client, session, monkeypatch):
    from test_api_flow import context, conversation_sync

    source_id = conversation_sync(client, {}, context("等待同步候选人", "谢女士", "pending-retention")).json()["candidate_source_id"]
    source = session.get(CandidateSource, source_id)
    source.last_seen_at = now() - timedelta(days=31)
    session.commit()
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(data_retention_worker, "SessionLocal", factory)
    monkeypatch.setattr(data_retention_worker, "get_settings", lambda: get_settings().model_copy(update={"candidate_cache_days": 30}))
    result = data_retention_worker.process_retention()
    session.expire_all()
    kept = session.get(CandidateSource, source_id)
    assert result["candidates_minimized"] == 0
    assert kept is not None
    assert kept.candidate_display_name == "等待同步候选人"
    assert kept.data_minimized_at is None


def test_failed_candidate_sync_payload_is_cleared_after_retention(client, session, monkeypatch):
    from test_api_flow import context, conversation_sync

    source_id = conversation_sync(client, {}, context("失败载荷候选人", "谢女士", "failed-retention")).json()["candidate_source_id"]
    row = session.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.candidate_source_id == source_id))
    row.status = "FAILED"
    row.updated_at = now() - timedelta(days=31)
    session.commit()
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(data_retention_worker, "SessionLocal", factory)
    monkeypatch.setattr(data_retention_worker, "get_settings", lambda: get_settings().model_copy(update={"failed_task_retention_days": 30}))
    result = data_retention_worker.process_retention()
    session.expire_all()
    row = session.get(CandidateSyncOutbox, row.id)
    assert result["failed_sync_payloads"] == 1
    assert row.payload_json == {}
    assert row.last_error is None
