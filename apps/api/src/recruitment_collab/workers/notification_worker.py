from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import exists, or_, select, update

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.feishu import FeishuCardBuilder, MockFeishuClient, RealFeishuClient
from recruitment_collab.infrastructure.models import Notification, NotificationOutbox, Recruiter, RecruitmentSetting, now
from recruitment_collab.workers.heartbeat import record_worker_heartbeat
from recruitment_collab.workers.retry import schedule_retry

PROCESSING_TIMEOUT_MINUTES = 5


def _claim_batch(limit: int, session):
    """Claim rows in a short transaction; never hold DB locks during HTTP."""
    stale_before = now() - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
    statement = (
        select(NotificationOutbox)
        .where(
            or_(
                ~exists(select(RecruitmentSetting.id).where(RecruitmentSetting.company_id == NotificationOutbox.company_id)),
                exists(select(RecruitmentSetting.id).where(RecruitmentSetting.company_id == NotificationOutbox.company_id, RecruitmentSetting.reset_in_progress.is_(False))),
            ),
            or_(
                (NotificationOutbox.status == "PENDING") & (NotificationOutbox.next_retry_at <= now()),
                (NotificationOutbox.status == "PROCESSING") & (NotificationOutbox.updated_at <= stale_before),
            )
        )
        .order_by(NotificationOutbox.created_at)
        .limit(limit)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    rows = session.scalars(statement).all()
    for row in rows:
        row.status = "PROCESSING"
    session.commit()
    return rows


async def process_batch(limit: int = 20) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        # Reset/retry rules are applied before claiming. The external call is
        # intentionally outside this transaction so a slow provider cannot
        # hold row locks for the duration of the request.
        session.execute(update(NotificationOutbox).where(NotificationOutbox.status == "PROCESSING", NotificationOutbox.updated_at <= now() - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)).values(status="PENDING"))
        session.commit()
        rows = _claim_batch(limit, session)
        client = MockFeishuClient(session) if settings.feishu_mode == "mock" else RealFeishuClient(settings)
        try:
            for row in rows:
                try:
                    card = FeishuCardBuilder().duplicate_card(row.payload_json)
                    recipient = row.recipient_recruiter_id
                    if settings.feishu_mode != "mock":
                        recruiter = session.get(Recruiter, row.recipient_recruiter_id)
                        if not recruiter or not recruiter.feishu_open_id:
                            raise ValueError("recipient has no Feishu open_id mapping")
                        recipient = recruiter.feishu_open_id
                    result = await client.send_card(recipient, card, idempotency_key=row.idempotency_key)
                    # Re-read after the provider call. A stale worker may have
                    # been reclaimed, so never overwrite a newer terminal row.
                    current = session.get(NotificationOutbox, row.id)
                    if not current or current.status != "PROCESSING":
                        continue
                    current.status, current.sent_at, current.last_error = "SENT", now(), None
                    current.payload_json = {}
                    session.add(
                        Notification(
                            company_id=current.company_id,
                            outbox_id=current.id,
                            recipient_recruiter_id=current.recipient_recruiter_id,
                            provider="MOCK_FEISHU" if settings.feishu_mode == "mock" else "FEISHU",
                            provider_message_id=result.provider_message_id,
                            status="SENT",
                            sent_at=now(),
                        )
                    )
                except Exception as exc:
                    current = session.get(NotificationOutbox, row.id)
                    if current and current.status == "PROCESSING":
                        schedule_retry(current, exc, "notification delivery")
                session.commit()
        finally:
            if isinstance(client, RealFeishuClient):
                await client.aclose()
        return len(rows)


async def main() -> None:
    while True:
        try:
            processed = await process_batch()
            record_worker_heartbeat("notification-worker", processed)
        except Exception as exc:
            record_worker_heartbeat("notification-worker", error_code=type(exc).__name__, force=True)
            raise
        await asyncio.sleep(1 if processed else 5)


if __name__ == "__main__":
    asyncio.run(main())
