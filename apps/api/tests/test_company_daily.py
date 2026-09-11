from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from conftest import login
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.infrastructure.models import BossAccountAssignment, BossCompanyDailyConfig, BossCompanyDailyRow, RecruitmentAccount
from recruitment_collab.workers import company_daily_worker


def test_daily_admin_login_never_reassigns_boss_accounts(session):
    import pytest

    from recruitment_collab.api.routes import _complete_company_daily_login
    from recruitment_collab.application.collaboration import ApplicationError
    from recruitment_collab.infrastructure.feishu import FeishuIdentity
    from recruitment_collab.infrastructure.models import FeishuBindingAttempt, Recruiter
    setup_config(session)
    admin = session.scalar(select(Recruiter).where(Recruiter.role == "ADMIN"))
    admin.feishu_open_id = "daily-admin"
    attempt = FeishuBindingAttempt(company_id=admin.company_id, account_display_name="谢女士", action="company_daily_login", state_hash="daily-state", device_id="test-device", expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
    session.add(attempt)
    session.commit()
    before = [(r.boss_account_id, r.feishu_recruiter_id) for r in session.scalars(select(BossAccountAssignment))]
    with pytest.raises(ApplicationError):
        _complete_company_daily_login(session, attempt, FeishuIdentity("not-admin", None, "普通成员"))
    assert attempt.status == "PENDING"
    assert _complete_company_daily_login(session, attempt, FeishuIdentity("daily-admin", None, "管理员")).status_code == 200
    assert attempt.status == "APPROVED" and attempt.recruiter_id == admin.id
    assert before == [(r.boss_account_id, r.feishu_recruiter_id) for r in session.scalars(select(BossAccountAssignment))]


def setup_config(session):
    account = session.scalar(select(RecruitmentAccount).where(RecruitmentAccount.account_display_name == "谢女士"))
    config = BossCompanyDailyConfig(company_id=account.company_id, collector_account_id=account.id, collector_name="谢女士", app_token="test", table_id="daily")
    session.add_all([config, BossAccountAssignment(company_id=account.company_id, boss_account_id=account.id, feishu_recruiter_id=account.recruiter_id)])
    session.commit()
    return config


def payload():
    day = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    return {"account_display_name": "谢女士", "metric_date": day, "source_updated_at": f"{day}T23:00:00+08:00", "rows": [
        {"boss_name": name, "boss_viewed_talent": n, "boss_started_chat": 0, "boss_communication": 43, "talent_viewed_boss": 172, "talent_started_chat": 26}
        for name, n in [("未登录成员", 42), ("谢女士", 0)]]}


def test_daily_whole_company_idempotency_permissions_and_date(client, session):
    setup_config(session)
    owner = login(client, "admin@example.com")
    other = login(client, "jiali@example.com")
    assert client.get("/api/v1/plugin/company-daily-data/plan", headers=owner).json()["enabled"] is True
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    scheduled_day = local_now.date() if (local_now.hour, local_now.minute) >= (23, 30) else local_now.date() - timedelta(days=1)
    assert client.get("/api/v1/plugin/company-daily-data/plan?scheduled=true", headers=owner).json()["dates"] == [scheduled_day.isoformat()]
    assert client.get("/api/v1/plugin/company-daily-data/plan", headers=other).json() == {"enabled": False}
    data = payload()
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=other, json=data).status_code == 403
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json={**data, "account_display_name": "珈莉"}).status_code == 403
    for _ in range(2):
        response = client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json=data)
        assert response.status_code == 200, response.text
        assert response.json()["row_count"] == 2
    rows = session.scalars(select(BossCompanyDailyRow)).all()
    assert len(rows) == 2
    assert all(row.version == 1 for row in rows)
    assert {row.boss_name for row in rows} == {"未登录成员", "谢女士"}
    data["rows"][0]["boss_viewed_talent"] = 44
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json=data).status_code == 200
    row = session.scalar(select(BossCompanyDailyRow).where(BossCompanyDailyRow.boss_name == "未登录成员"))
    session.refresh(row)
    assert row.version == 2 and row.metrics["boss_viewed_talent"] == 44

    # A new calendar day creates a fresh row per BOSS account instead of
    # overwriting the previous day's snapshot.
    next_day = (datetime.fromisoformat(data["metric_date"]) + timedelta(days=1)).date().isoformat()
    next_day_data = {
        **data,
        "metric_date": next_day,
        "source_updated_at": f"{next_day}T23:00:00+08:00",
        "rows": [{**item} for item in data["rows"]],
    }
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json=next_day_data).status_code == 200
    session.expire_all()
    rows = session.scalars(select(BossCompanyDailyRow)).all()
    assert len(rows) == 4
    assert {row.metric_date for row in rows} == {data["metric_date"], next_day}
    assert all(
        len([row for row in rows if row.boss_name == name]) == 2
        for name in {"未登录成员", "谢女士"}
    )

    data["rows"][0]["boss_viewed_talent"] = None
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json=data).status_code == 422
    assert client.post("/api/v1/plugin/company-daily-data/batch", headers=owner, json={**payload(), "metric_date": "2099-01-01"}).status_code == 422


def test_daily_delivery_failure_retries_without_duplicate_and_keeps_candidate_table_separate(client, session, monkeypatch):
    setup_config(session)
    client.post("/api/v1/plugin/company-daily-data/batch", headers=login(client, "admin@example.com"), json=payload())
    monkeypatch.setattr(company_daily_worker, "SessionLocal", sessionmaker(bind=session.bind, expire_on_commit=False))
    calls = []
    def fail(self, *args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(company_daily_worker.DailyBitableClient, "upsert_daily", fail)
    assert company_daily_worker.process_batch() == 2
    session.expire_all()
    rows = session.scalars(select(BossCompanyDailyRow)).all()
    assert all(row.status == "PENDING" and row.retry_count == 1 for row in rows)
    for row in rows:
        row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    def succeed(self, day, name, metrics, record_id):
        calls.append((self.candidate_table_id, name))
        return record_id or f"rec-{name}"
    monkeypatch.setattr(company_daily_worker.DailyBitableClient, "upsert_daily", succeed)
    assert company_daily_worker.process_batch() == 2
    assert company_daily_worker.process_batch() == 0
    assert all(table == "daily" for table, _ in calls)
    session.expire_all()
    assert all(row.status == "SENT" and row.feishu_record_id for row in session.scalars(select(BossCompanyDailyRow)).all())


def test_empty_feishu_table_null_items():
    from unittest.mock import Mock
    from recruitment_collab.infrastructure.daily_bitable import DailyBitableClient
    client = object.__new__(DailyBitableClient)
    client._table_url = lambda _: 'https://example.test/records'
    client._data = lambda _: {'items': None, 'has_more': False}
    assert list(client._record_pages(Mock(), {})) == []
