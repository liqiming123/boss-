from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select

from recruitment_collab.application.collaboration import ApplicationError
from recruitment_collab.infrastructure.models import BossAccountAssignment, BossCompanyDailyConfig, BossCompanyDailyRow, RecruitmentAccount, now

COUNTERS = ("boss_viewed_talent", "boss_started_chat", "boss_communication", "talent_viewed_boss", "talent_started_chat")
Count = Annotated[StrictInt, Field(ge=0, le=10000000)]


class DailyRowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    boss_name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    boss_viewed_talent: Count
    boss_started_chat: Count
    boss_communication: Count
    talent_viewed_boss: Count
    talent_started_chat: Count


class DailyBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_display_name: str = Field(min_length=1, max_length=100)
    metric_date: date
    source_updated_at: datetime
    rows: list[DailyRowInput] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_batch(self):
        if self.source_updated_at.tzinfo is None:
            raise ValueError("source_updated_at needs timezone")
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if self.metric_date > local_now.date() or (self.metric_date == local_now.date() and (local_now.hour, local_now.minute) < (23, 30)):
            raise ValueError("Only completed dates can be synchronized")
        if self.source_updated_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Source timestamp is in the future")
        names = [row.boss_name.strip() for row in self.rows]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate BOSS names need manual disambiguation")
        return self


def collector_config(db, actor, account_name=None):
    if actor.role.upper() != "ADMIN":
        return None, None
    config = db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.company_id == actor.company_id, BossCompanyDailyConfig.enabled.is_(True)))
    if not config or (account_name is not None and account_name != config.collector_name):
        return None, None
    account = db.scalar(select(RecruitmentAccount).where(RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.account_display_name == config.collector_name, RecruitmentAccount.status == "ACTIVE"))
    return config, account


def ingest_daily(db, config, body):
    source_time = body.source_updated_at.astimezone(timezone.utc)
    # Serialize a whole date, including concurrent devices, on the config row.
    db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.id == config.id).with_for_update())
    for item in body.rows:
        name = item.boss_name.strip()
        values = {key: getattr(item, key) for key in COUNTERS}
        row = db.scalar(select(BossCompanyDailyRow).where(BossCompanyDailyRow.company_id == config.company_id, BossCompanyDailyRow.metric_date == body.metric_date.isoformat(), BossCompanyDailyRow.boss_name == name))
        if row:
            previous_time = row.source_updated_at.replace(tzinfo=timezone.utc) if row.source_updated_at.tzinfo is None else row.source_updated_at
            if previous_time > source_time:
                continue
            row.source_updated_at = source_time
            if row.metrics != values or row.status == "FAILED":
                row.metrics = values
                row.version += 1
                row.status, row.retry_count, row.last_error, row.next_retry_at = "PENDING", 0, None, now()
        else:
            db.add(BossCompanyDailyRow(company_id=config.company_id, collector_account_id=config.collector_account_id,
                metric_date=body.metric_date.isoformat(), boss_name=name, metrics=values, source_updated_at=source_time))
    if not config.last_collected_date or config.last_collected_date <= body.metric_date.isoformat():
        config.last_collected_date, config.last_collected_at = body.metric_date.isoformat(), now()
    db.commit()
    return {"status": "QUEUED", "metric_date": body.metric_date.isoformat(), "row_count": len(body.rows)}


def require_collector(db, actor, name):
    config, _ = collector_config(db, actor, name)
    if not config:
        raise ApplicationError("DAILY_COLLECTOR_FORBIDDEN", "此设备没有公司日报采集权限", 403)
    return config
