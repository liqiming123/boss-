import asyncio
import hashlib
import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import Base
from recruitment_collab.infrastructure.feishu import FeishuCallbackVerifier, FeishuCardBuilder, RealFeishuClient
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


def test_real_feishu_send_includes_json_content_type_and_provider_error():
    class FakeResponse:
        status_code = 400
        reason_phrase = "Bad Request"
        is_error = True

        def json(self):
            return {"code": 230002, "msg": "invalid receive_id"}

    class FakeClient:
        async def post(self, url, **kwargs):
            assert kwargs["headers"]["Content-Type"] == "application/json; charset=utf-8"
            assert kwargs["params"] == {"receive_id_type": "open_id"}
            assert kwargs["json"]["msg_type"] == "interactive"
            return FakeResponse()

        async def aclose(self):
            pass

    async def exercise():
        settings = get_settings().model_copy(update={"feishu_app_id": "app", "feishu_app_secret": "secret"})
        client = RealFeishuClient(settings, FakeClient())
        client._token, client._expires_at = "tenant-token", time.time() + 3600
        try:
            await client.send_card("ou-recipient", FeishuCardBuilder().duplicate_card({}))
        except RuntimeError as exc:
            assert "code=230002" in str(exc)
            assert "invalid receive_id" in str(exc)
        else:
            raise AssertionError("expected Feishu provider error")
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_duplicate_card_is_informational_without_actions():
    card = FeishuCardBuilder().duplicate_card({"candidate_name": "小明"})
    assert all(element.get("tag") != "action" for element in card["elements"])


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
