from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.infrastructure.models import BossAccountAssignment, PluginDevice, Recruiter, RecruitmentAccount, WorkerHeartbeat, now
from recruitment_collab.workers import heartbeat


def test_management_login_uses_active_company_scope(client):
    admin = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "dev-password"})
    assert admin.status_code == 200
    assert admin.json()["user"]["role"] == "ADMIN"

    recruiter = client.post("/api/v1/auth/login", json={"email": "xie@example.com", "password": "dev-password"})
    assert recruiter.status_code == 200
    assert recruiter.json()["user"]["role"] == "RECRUITER"


def test_management_route_uses_active_company_scope(client):
    from conftest import login

    response = client.get("/api/v1/admin/conflicts", headers=login(client, "xie@example.com"))
    assert response.status_code == 200


def test_operations_reports_workers_queues_bindings_and_allows_device_revoke(client, session):
    from conftest import login
    from test_api_flow import context, conversation_sync, message_sent

    admin_headers = login(client, "admin@example.com")
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-operations"
    recruiter.feishu_display_name = "运维测试用户"
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.recruiter_id == recruiter.id))
    assert account is not None
    session.add(
        BossAccountAssignment(
            company_id=recruiter.company_id,
            boss_account_id=account.id,
            feishu_recruiter_id=recruiter.id,
            feishu_open_id="ou-operations",
            feishu_display_name="运维测试用户",
        )
    )
    device = PluginDevice(
        company_id=recruiter.company_id,
        recruiter_id=recruiter.id,
        device_id="operations-device",
        device_name="Chrome test",
        refresh_token_hash="hashed",
    )
    session.add(device)
    for name in ("candidate-sync-worker", "notification-worker", "data-retention-worker"):
        session.add(WorkerHeartbeat(worker_name=name, status="HEALTHY", last_seen_at=now(), last_success_at=now()))
    session.commit()
    seeded = context("运维候选人", "谢女士", "operations")
    assert conversation_sync(client, login(client, "xie@example.com"), seeded).status_code == 200
    # The row exists now, so a real send registers its audit event.
    assert message_sent(client, login(client, "xie@example.com"), seeded).status_code == 200

    response = client.get("/api/v1/admin/operations", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["candidate_sources"] == 1
    assert body["metrics"]["message_sent_total"] == 1
    assert body["communication_summary"][0]["recruiter_name"] == "运维测试用户"
    assert body["communication_summary"][0]["message_count"] == 1
    assert body["metrics"]["candidate_sync_pending"] == 1
    assert body["metrics"]["active_devices"] == 1
    assert all(worker["status"] == "HEALTHY" for worker in body["workers"])
    assert {alert["id"] for alert in body["alerts"]} >= {"candidate-sync-pending", "snapshot-missing", "checkpoint-missing"}
    binding = next(item for item in body["bindings"] if item["recruiter_id"] == recruiter.id)
    assert binding["bound"] is True
    assert binding["devices"][0]["device_name"] == "Chrome test"

    candidate_rows = client.get("/api/v1/admin/candidate-sources", headers=admin_headers).json()
    assert candidate_rows[0]["message_sent_count"] == 1

    revoked = client.post("/api/v1/admin/devices/operations-device/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    session.refresh(device)
    assert device.status == "REVOKED" and device.refresh_token_hash is None


def test_worker_heartbeat_initializes_and_accumulates_a_single_bounded_row(session, monkeypatch):
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(heartbeat, "SessionLocal", factory)
    monkeypatch.setattr(heartbeat, "_last_write", {})
    heartbeat.record_worker_heartbeat("test-worker", processed=3, force=True)
    heartbeat.record_worker_heartbeat("test-worker", processed=2, force=True)
    rows = session.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.worker_name == "test-worker")).all()
    assert len(rows) == 1
    assert rows[0].status == "HEALTHY"
    assert rows[0].total_processed == 5


def test_daily_boss_metrics_are_upserted_by_account_and_date(client, session):
    from conftest import login
    headers = login(client, "xie@example.com")
    payload = {"account_display_name": "谢女士", "metric_date": "2026-09-08", "boss_viewed_talent": 42, "boss_started_chat": 18, "boss_communication": 43, "talent_viewed_boss": 166, "talent_started_chat": 24}
    first = client.post("/api/v1/plugin/boss-daily-metrics", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    second = client.post("/api/v1/plugin/boss-daily-metrics", headers=headers, json={**payload, "boss_communication": 44})
    assert second.status_code == 200
    assert second.json()["boss_communication"] == 44
    assert client.get("/api/v1/admin/boss-daily-metrics", headers=login(client, "admin@example.com")).json()[0]["metric_date"] == "2026-09-08"
