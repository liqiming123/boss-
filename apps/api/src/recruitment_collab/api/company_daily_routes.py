from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from recruitment_collab.api.dependencies import Actor, plugin_actor
from recruitment_collab.application.company_daily import DailyBatchInput, collector_config, ingest_daily, require_collector
from recruitment_collab.infrastructure.database import get_db

router = APIRouter(prefix="/api/v1/plugin/company-daily-data")


@router.get("/plan")
def plan(refresh: bool = False, today: bool = False, scheduled: bool = False, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)):
    config, account = collector_config(db, actor)
    if not config:
        return {"enabled": False}
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    yesterday = local_now.date() - timedelta(days=1)
    today_date = yesterday + timedelta(days=1)
    if scheduled:
        target = today_date if (local_now.hour, local_now.minute) >= (23, 30) else yesterday
        return {"enabled": True, "account_display_name": config.collector_name, "dates": [target.isoformat()], "timezone": "Asia/Shanghai", "data_url": "https://www.zhipin.com/web/frame/enterprise/recruit/data"}
    if today:
        return {"enabled": True, "account_display_name": config.collector_name, "dates": [today_date.isoformat()], "timezone": "Asia/Shanghai", "data_url": "https://www.zhipin.com/web/frame/enterprise/recruit/data"}
    dates = []
    month_start = yesterday.replace(day=1)
    if refresh:
        # Manual check is an explicit backfill request: collect every completed
        # day in the current month, including days already stored (upsert is
        # idempotent and lets the provider correct delayed aggregation).
        start = month_start
    elif config.last_collected_date:
        start = max(date.fromisoformat(config.last_collected_date) + timedelta(days=1), month_start)
    else:
        start = month_start
    dates = [(start + timedelta(days=i)).isoformat() for i in range(max(0, (yesterday - start).days + 1))]
    return {"enabled": True, "account_display_name": config.collector_name,
        "dates": dates, "timezone": "Asia/Shanghai", "data_url": "https://www.zhipin.com/web/frame/enterprise/recruit/data"}


@router.post("/batch")
def batch(body: DailyBatchInput, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)):
    return ingest_daily(db, require_collector(db, actor, body.account_display_name), body)
