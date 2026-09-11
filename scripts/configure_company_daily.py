"""Configure the explicitly supplied Bitable child table, without logging secrets."""
import argparse
import json

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.bitable import BitableSyncClient
from recruitment_collab.infrastructure.daily_bitable import DailyBitableClient
from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import (
    BossCompanyDailyConfig,
    Company,
    RecruitmentAccount,
)
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table-url", required=True)
    parser.add_argument("--collector", required=True)
    args = parser.parse_args()
    settings = get_settings()
    app_token, table_id = BitableSyncClient.parse_table_url(args.table_url)
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.code == settings.plugin_company_code))
        if not company:
            raise SystemExit("Configured company not found")
        accounts = db.scalars(select(RecruitmentAccount).where(RecruitmentAccount.company_id == company.id,
            RecruitmentAccount.account_display_name == args.collector, RecruitmentAccount.status == "ACTIVE")).all()
        if len(accounts) > 1:
            raise SystemExit("Collector account is ambiguous")
        current = db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.company_id == company.id))
        if current and current.table_id != table_id:
            raise SystemExit("Destination changed; explicit data migration required")
        result = DailyBitableClient(settings, app_token=app_token, candidate_table_id=table_id).configure()
        if not current:
            current = BossCompanyDailyConfig(company_id=company.id, collector_account_id=accounts[0].id if accounts else None, collector_name=args.collector,
                app_token=result["app_token"], table_id=table_id)
            db.add(current)
        current.enabled = True
        current.collector_account_id = accounts[0].id if accounts else None
        current.collector_name = args.collector
        db.commit()
        print(json.dumps({"configured": True, "collector": args.collector, "table_id": table_id, "fields": result["fields"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
