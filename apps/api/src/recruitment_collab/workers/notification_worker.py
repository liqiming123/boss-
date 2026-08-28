from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.feishu import FeishuCardBuilder, MockFeishuClient, RealFeishuClient
from recruitment_collab.infrastructure.models import Notification, NotificationOutbox, Recruiter, now

MAX_RETRIES = 5


async def process_batch(limit: int = 20) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        statement = select(NotificationOutbox).where(NotificationOutbox.status == "PENDING", NotificationOutbox.next_retry_at <= now()).order_by(NotificationOutbox.created_at).limit(limit)
        if session.bind and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = session.scalars(statement).all()
        client = MockFeishuClient(session) if settings.feishu_mode == "mock" else RealFeishuClient(settings)
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
                session.add(Notification(company_id=row.company_id, outbox_id=row.id, recipient_recruiter_id=row.recipient_recruiter_id, provider="MOCK_FEISHU" if settings.feishu_mode == "mock" else "FEISHU", provider_message_id=result.provider_message_id, status="SENT", sent_at=now()))
            except Exception as exc:
                row.retry_count += 1
                row.last_error = f"{type(exc).__name__}: notification delivery failed"[:500]
                row.status = "FAILED" if row.retry_count >= MAX_RETRIES else "PENDING"
                row.next_retry_at = now() + timedelta(seconds=min(3600, 2 ** row.retry_count * 5))
            session.commit()
        return len(rows)


async def main() -> None:
    while True:
        processed = await process_batch()
        await asyncio.sleep(1 if processed else 5)


if __name__ == "__main__":
    asyncio.run(main())
