from datetime import timedelta

from sqlalchemy import or_, select

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.daily_bitable import DailyBitableClient
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import BossCompanyDailyConfig, BossCompanyDailyRow, now


def process_batch(limit=10):
    count = 0
    for _ in range(limit):
        with SessionLocal() as db:
            query = select(BossCompanyDailyRow).join(BossCompanyDailyConfig, BossCompanyDailyConfig.company_id == BossCompanyDailyRow.company_id).where(
                BossCompanyDailyConfig.enabled.is_(True), or_(
                    (BossCompanyDailyRow.status == "PENDING") & (BossCompanyDailyRow.next_retry_at <= now()),
                    (BossCompanyDailyRow.status == "PROCESSING") & (BossCompanyDailyRow.updated_at < now() - timedelta(minutes=5)),
                )).order_by(BossCompanyDailyRow.metric_date, BossCompanyDailyRow.id).limit(1).with_for_update(skip_locked=True)
            row = db.scalar(query)
            if not row:
                break
            config = db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.company_id == row.company_id))
            row.status = "PROCESSING"
            task = (row.id, row.version, row.metric_date, row.boss_name, dict(row.metrics), row.feishu_record_id, config.app_token, config.table_id)
            db.commit()
        row_id, version, day, name, metrics, record_id, app_token, table_id = task
        try:
            record_id = DailyBitableClient(get_settings(), app_token=app_token, candidate_table_id=table_id).upsert_daily(day, name, metrics, record_id)
            with SessionLocal() as db:
                row = db.get(BossCompanyDailyRow, row_id)
                if row:
                    row.feishu_record_id = record_id
                    row.status = "SENT" if row.version == version else "PENDING"
                    row.synced_at, row.last_error = now(), None
                    row.next_retry_at = now()
                    db.commit()
        except Exception as exc:
            with SessionLocal() as db:
                row = db.get(BossCompanyDailyRow, row_id)
                if row and row.version == version:
                    row.retry_count += 1
                    row.status = "FAILED" if row.retry_count >= 5 else "PENDING"
                    row.next_retry_at = now() + timedelta(seconds=min(3600, 2 ** row.retry_count * 10))
                    row.last_error = type(exc).__name__
                    db.commit()
        count += 1
    return count
