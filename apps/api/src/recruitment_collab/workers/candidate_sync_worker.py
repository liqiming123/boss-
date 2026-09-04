from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.bitable import BitableSyncClient
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import CandidateSource, CandidateSyncOutbox, now
from recruitment_collab.workers.heartbeat import record_worker_heartbeat
from recruitment_collab.workers.retry import schedule_retry

PROCESSING_TIMEOUT_MINUTES = 5


@dataclass(frozen=True)
class ClaimedSync:
    row_id: str
    payload_version: int
    payload: dict[str, Any]
    record_id: str | None


def _claim_next() -> ClaimedSync | None:
    with SessionLocal() as session:
        stale_before = now() - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
        statement = (
            select(CandidateSyncOutbox)
            .where(
                or_(
                    (CandidateSyncOutbox.status == "PENDING") & (CandidateSyncOutbox.next_retry_at <= now()),
                    (CandidateSyncOutbox.status == "PROCESSING") & (CandidateSyncOutbox.updated_at <= stale_before),
                )
            )
            .order_by(CandidateSyncOutbox.created_at)
            .limit(1)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = session.scalar(statement)
        if not row:
            return None
        row.status = "PROCESSING"
        source = session.get(CandidateSource, row.candidate_source_id)
        claimed = ClaimedSync(row.id, row.payload_version, dict(row.payload_json), source.feishu_record_id if source else None)
        session.commit()
        return claimed


def _complete(task: ClaimedSync, synced_record_id: str | None) -> None:
    with SessionLocal() as session:
        row = session.get(CandidateSyncOutbox, task.row_id)
        if not row:
            return
        source = session.get(CandidateSource, row.candidate_source_id)
        if source and synced_record_id:
            source.feishu_record_id = synced_record_id
        if row.payload_version == task.payload_version:
            row.status = "SENT"
            row.synced_at = now()
            row.last_error = None
            row.payload_json = {}
        else:
            row.status = "PENDING"
            row.next_retry_at = now()
        session.commit()


def _fail(task: ClaimedSync, error: Exception) -> None:
    with SessionLocal() as session:
        row = session.get(CandidateSyncOutbox, task.row_id)
        if row:
            # Do not persist response bodies, candidate payloads or credentials.
            schedule_retry(row, error, "candidate sync")
            session.commit()


def process_batch(limit: int = 20) -> int:
    """Synchronize due rows without making the message-sent API wait on Feishu."""
    settings = get_settings()
    client = BitableSyncClient(settings)
    processed = 0
    while processed < limit:
        task = _claim_next()
        if not task:
            break
        try:
            _status, synced_record_id = client.upsert_candidate(task.payload, task.record_id)
            _complete(task, synced_record_id)
        except Exception as exc:
            _fail(task, exc)
        processed += 1
    return processed


async def main() -> None:
    while True:
        try:
            processed = process_batch()
            record_worker_heartbeat("candidate-sync-worker", processed)
        except Exception as exc:
            record_worker_heartbeat("candidate-sync-worker", error_code=type(exc).__name__, force=True)
            raise
        await asyncio.sleep(1 if processed else 5)


if __name__ == "__main__":
    asyncio.run(main())
