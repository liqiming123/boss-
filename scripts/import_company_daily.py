"""Import a reviewed, minimized browser report through the normal delivery queue."""
import argparse
import json
from pathlib import Path

import httpx
from sqlalchemy import select

from recruitment_collab.application.company_daily import DailyBatchInput, ingest_daily
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.daily_bitable import DailyBitableClient, METRIC_FIELDS
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import BossCompanyDailyConfig, BossCompanyDailyRow, Company
from recruitment_collab.workers.company_daily_worker import process_batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    body = DailyBatchInput.model_validate_json(Path(args.input).read_text())
    settings = get_settings()
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.code == settings.plugin_company_code))
        config = db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.company_id == company.id))
        if not config or not config.enabled or config.collector_name != body.account_display_name:
            raise SystemExit("Collector configuration does not match reviewed report")
        result = ingest_daily(db, config, body)
        token, table = config.app_token, config.table_id
    process_batch(100)
    with SessionLocal() as db:
        statuses = [(row.boss_name, row.status) for row in db.scalars(select(BossCompanyDailyRow).where(BossCompanyDailyRow.company_id == company.id, BossCompanyDailyRow.metric_date == body.metric_date.isoformat()))]
    report = DailyBitableClient(settings, app_token=token, candidate_table_id=table)
    with httpx.Client(timeout=30) as client:
        fields = [row.get("fields", {}) for row in report._record_pages(client, report._headers(client))]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    expected_stamp = int(datetime.combine(body.metric_date, datetime.min.time(), ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    reviewed = [row for row in fields if row.get("日期") == expected_stamp]
    expected = {row.boss_name: row.model_dump() for row in body.rows}
    if len(reviewed) != len(expected):
        raise SystemExit("DAILY_REVIEW_RECORD_COUNT_MISMATCH")
    for row in reviewed:
        name = report.plain_value(row.get("BOSS姓名"))
        if name not in expected or any(float(row.get(label, -1)) != expected[name][key] for key, label in METRIC_FIELDS.items()):
            raise SystemExit("DAILY_REVIEW_VALUE_MISMATCH")
    print(json.dumps({**result, "delivery": statuses, "table_records": len(fields), "verified_daily_records": len(reviewed), "verified_metric_cells": len(reviewed) * 5}, ensure_ascii=False))


if __name__ == "__main__":
    main()
