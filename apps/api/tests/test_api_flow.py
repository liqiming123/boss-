import uuid
from datetime import datetime, timezone

from conftest import login
from sqlalchemy import func, select

from recruitment_collab.infrastructure.models import Conflict, Engagement, NotificationOutbox


def context(name: str, account: str, suffix: str):
    return {"platform": "mock", "page_url": f"http://localhost:5174/candidate/{suffix}", "platform_candidate_id": None, "platform_id_scope": "UNKNOWN", "candidate_display_name": name, "job_display_name": "短视频编导", "account_display_name": account, "observed_at": datetime.now(timezone.utc).isoformat(), "client_event_id": str(uuid.uuid4()), "extractor_version": "mock-adapter-1"}


def test_view_does_not_engage_or_notify(client, session):
    headers = login(client, "xie@example.com")
    response = client.post("/api/v1/plugin/context/resolve", headers=headers, json=context("小明", "谢女士", "xie"))
    assert response.status_code == 200
    assert response.json()["result_type"] == "NO_HISTORY"
    assert session.scalar(select(func.count()).select_from(Engagement)) == 0
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0


def test_second_recruiter_action_creates_one_conflict_and_two_outbox(client, session):
    xie, jiali = login(client, "xie@example.com"), login(client, "jiali@example.com")
    first = client.post("/api/v1/plugin/context/resolve", headers=xie, json=context("小明", "谢女士", "xie")).json()
    client.post("/api/v1/plugin/events", headers=xie, json={"candidate_source_id": first["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": str(uuid.uuid4())})
    second = client.post("/api/v1/plugin/context/resolve", headers=jiali, json=context("小明", "珈莉", "jiali")).json()
    assert second["result_type"] == "SUSPECTED_DUPLICATE"
    key = str(uuid.uuid4())
    result = client.post("/api/v1/plugin/events", headers=jiali, json={"candidate_source_id": second["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": key})
    assert result.status_code == 200, result.text
    assert session.scalar(select(func.count()).select_from(Conflict)) == 1
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 2
    repeat = client.post("/api/v1/plugin/events", headers=jiali, json={"candidate_source_id": second["candidate_source_id"], "event_type": "CONTACTED", "idempotency_key": key})
    assert repeat.json()["idempotent"] is True
    assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 2


def test_continue_requires_reason(client):
    headers = login(client, "xie@example.com")
    source = client.post("/api/v1/plugin/context/resolve", headers=headers, json=context("张女士", "谢女士", "reason")).json()
    response = client.post("/api/v1/plugin/events", headers=headers, json={"candidate_source_id": source["candidate_source_id"], "event_type": "CONTINUED_AFTER_WARNING", "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REASON_REQUIRED"
