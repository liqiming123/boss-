from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.infrastructure.models import PluginDevice, Recruiter, WorkerHeartbeat, now
from recruitment_collab.workers import heartbeat


def test_production_admin_login_rejects_recruiter_and_accepts_admin(client):
    admin = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "dev-password"})
    assert admin.status_code == 200
    assert admin.json()["user"]["role"] == "ADMIN"

    recruiter = client.post("/api/v1/auth/login", json={"email": "xie@example.com", "password": "dev-password"})
    assert recruiter.status_code == 403
    assert recruiter.json()["error"]["code"] == "ADMIN_LOGIN_REQUIRED"


def test_admin_conflicts_route_rejects_recruiter_token(client):
    from conftest import login

    response = client.get("/api/v1/admin/conflicts", headers=login(client, "xie@example.com"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_operations_reports_workers_queues_bindings_and_allows_device_revoke(client, session):
    from conftest import login
    from test_api_flow import context, message_sent

    admin_headers = login(client, "admin@example.com")
    recruiter = session.scalar(select(Recruiter).where(Recruiter.display_name == "谢女士"))
    assert recruiter is not None
    recruiter.feishu_open_id = "ou-operations"
    recruiter.feishu_display_name = "运维测试用户"
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
    assert message_sent(client, login(client, "xie@example.com"), context("运维候选人", "谢女士", "operations")).status_code == 200

    response = client.get("/api/v1/admin/operations", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["candidate_sources"] == 1
    assert body["metrics"]["candidate_sync_pending"] == 1
    assert body["metrics"]["active_devices"] == 1
    assert all(worker["status"] == "HEALTHY" for worker in body["workers"])
    assert {alert["id"] for alert in body["alerts"]} >= {"candidate-sync-pending", "snapshot-missing", "checkpoint-missing"}
    binding = next(item for item in body["bindings"] if item["recruiter_id"] == recruiter.id)
    assert binding["bound"] is True
    assert binding["devices"][0]["device_name"] == "Chrome test"

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
