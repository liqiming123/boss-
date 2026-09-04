import asyncio
import hashlib
import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import Base
from recruitment_collab.infrastructure.feishu import FeishuCallbackVerifier
from recruitment_collab.infrastructure.models import Company, MockFeishuMessage, NotificationOutbox, Recruiter
from recruitment_collab.infrastructure.security import hash_password
from recruitment_collab.workers import notification_worker


def test_callback_signature_checks_freshness():
    verifier = FeishuCallbackVerifier("encrypt-key", "verify-token")
    timestamp, nonce, body = str(int(time.time())), "nonce", "encrypted"
    signature = hashlib.sha256(f"{timestamp}{nonce}encrypt-key{body}".encode()).hexdigest()
    assert verifier.verify_signature(timestamp, nonce, body, signature)
    assert not verifier.verify_signature("1", nonce, body, signature)
    assert verifier.verify_token("verify-token")
    assert not verifier.verify_token("wrong")


def test_mock_worker_delivers_and_persists_message(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        company = Company(name="通知测试", code="NOTIFY")
        db.add(company)
        db.flush()
        recruiter = Recruiter(
            company_id=company.id,
            display_name="谢女士",
            email="notify@example.com",
            role="RECRUITER",
            password_hash=hash_password("dev-password"),
        )
        db.add(recruiter)
        db.flush()
        db.add(
            NotificationOutbox(
                company_id=company.id,
                event_type="CONFLICT_CREATED",
                aggregate_type="conflict",
                aggregate_id="conflict-id",
                recipient_recruiter_id=recruiter.id,
                payload_json={"candidate_name": "小明", "match_reason": "同名同岗位"},
                idempotency_key="worker-test",
            )
        )
        db.commit()
    monkeypatch.setattr(notification_worker, "SessionLocal", factory)
    mock_settings = get_settings().model_copy(update={"app_env": "test", "feishu_mode": "mock"})
    monkeypatch.setattr(notification_worker, "get_settings", lambda: mock_settings)
    assert asyncio.run(notification_worker.process_batch()) == 1
    with factory() as db:
        outbox = db.scalar(select(NotificationOutbox))
        assert outbox.status == "SENT"
        assert db.scalar(select(func.count()).select_from(MockFeishuMessage)) == 1
