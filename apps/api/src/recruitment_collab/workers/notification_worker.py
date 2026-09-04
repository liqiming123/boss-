from __future__ import annotations

import asyncio

from sqlalchemy import select

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.feishu import FeishuCardBuilder, MockFeishuClient, RealFeishuClient
from recruitment_collab.infrastructure.models import Notification, NotificationOutbox, Recruiter, now
from recruitment_collab.workers.heartbeat import record_worker_heartbeat
from recruitment_collab.workers.retry import schedule_retry


async def process_batch(limit: int = 20) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        statement = (
            select(NotificationOutbox)
            .where(NotificationOutbox.status == "PENDING", NotificationOutbox.next_retry_at <= now())
            .order_by(NotificationOutbox.created_at)
            .limit(limit)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = session.scalars(statement).all()
        client = MockFeishuClient(session) if settings.feishu_mode == "mock" else RealFeishuClient(settings)
        try:
            for row in rows:
                row.status = "PROCESSING"
                session.flush()
                try:
                    card = FeishuCardBuilder().duplicate_card(row.payload_json)
                    recipient = row.recipient_recruiter_id
                    if settings.feishu_mode != "mock":
                        recruiter = session.get(Recruiter, row.recipient_recruiter_id)
                        if not recruiter or not recruiter.feishu_open_id:
                            raise ValueError("recipient has no Feishu open_id mapping")
                        recipient = recruiter.feishu_open_id
                    result = await client.send_card(recipient, card)
                    row.status, row.sent_at, row.last_error = "SENT", now(), None
                    row.payload_json = {}
                    session.add(
                        Notification(
                            company_id=row.company_id,
                            outbox_id=row.id,
                            recipient_recruiter_id=row.recipient_recruiter_id,
                            provider="MOCK_FEISHU" if settings.feishu_mode == "mock" else "FEISHU",
                            provider_message_id=result.provider_message_id,
                            status="SENT",
                            sent_at=now(),
                        )
                    )
                except Exception as exc:
                    schedule_retry(row, exc, "notification delivery")
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
