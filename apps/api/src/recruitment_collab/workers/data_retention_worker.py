from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import delete, update

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import (
    AuditLog,
    CandidateSource,
    CandidateSyncOutbox,
    DuplicateLookupAlert,
    FeishuBindingAttempt,
    NotificationOutbox,
    PluginDiagnostic,
    RecruitmentEvent,
    now,
)
from recruitment_collab.workers.heartbeat import record_worker_heartbeat


def process_retention() -> dict[str, int]:
    """Keep Feishu as the business record and retain only bounded operational data."""
    settings = get_settings()
    current = now()
    counts: dict[str, int] = {}
    with SessionLocal() as session:
        delete_specs = (
            ("diagnostics", PluginDiagnostic, PluginDiagnostic.created_at, settings.diagnostic_retention_days),
            ("events", RecruitmentEvent, RecruitmentEvent.created_at, settings.event_retention_days),
            ("audits", AuditLog, AuditLog.created_at, settings.audit_retention_days),
            ("lookup_alerts", DuplicateLookupAlert, DuplicateLookupAlert.last_detected_at, settings.audit_retention_days),
            ("binding_attempts", FeishuBindingAttempt, FeishuBindingAttempt.expires_at, 7),
        )
        for key, model, date_column, retention_days in delete_specs:
            result = session.execute(delete(model).where(date_column < current - timedelta(days=retention_days)))
            counts[key] = int(getattr(result, "rowcount", 0) or 0)

        failed_before = current - timedelta(days=settings.failed_task_retention_days)
        failed_notifications = session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.status.in_({"FAILED", "CANCELLED"}), NotificationOutbox.updated_at < failed_before)
            .values(payload_json={}, last_error=None)
        )
        failed_sync = session.execute(
            update(CandidateSyncOutbox)
            .where(CandidateSyncOutbox.status == "FAILED", CandidateSyncOutbox.updated_at < failed_before)
            .values(payload_json={}, last_error=None)
        )
        minimized = session.execute(
            update(CandidateSource)
            .where(
                CandidateSource.feishu_record_id.is_not(None),
                CandidateSource.last_seen_at < current - timedelta(days=settings.candidate_cache_days),
                CandidateSource.data_minimized_at.is_(None),
                CandidateSource.snapshot_status != "PENDING",
            )
            .values(
                candidate_display_name="已同步至飞书",
                candidate_normalized_name="",
                candidate_age=None,
                candidate_experience=None,
                candidate_education=None,
                platform_candidate_id=None,
                page_url_hash="",
                raw_job_name="",
                status_evidence=None,
                resume_tokens_json=[],
                resume_hash=None,
                resume_status="MINIMIZED",
                resume_file_name=None,
                data_minimized_at=current,
            )
        )
        counts.update(
            failed_payloads=int(getattr(failed_notifications, "rowcount", 0) or 0),
            failed_sync_payloads=int(getattr(failed_sync, "rowcount", 0) or 0),
            candidates_minimized=int(getattr(minimized, "rowcount", 0) or 0),
        )
        session.commit()
    return counts


async def main() -> None:
    while True:
        try:
            counts = process_retention()
            record_worker_heartbeat("data-retention-worker", sum(counts.values()), force=True)
        except Exception as exc:
            record_worker_heartbeat("data-retention-worker", error_code=type(exc).__name__, force=True)
            raise
        for _ in range(120):
            await asyncio.sleep(30)
            record_worker_heartbeat("data-retention-worker")


if __name__ == "__main__":
    asyncio.run(main())
