"""Backfill the active Feishu table config from the first deployment's env."""

import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None

revision = "0023_seed_active_feishu_table"
down_revision = "0022_recruitment_workspace_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM recruitment_feishu_bitable_configs WHERE status = 'ACTIVE' LIMIT 1")).first():
        return
    values = dotenv_values(Path.cwd() / ".env") if dotenv_values else {}
    app_token = (os.getenv("FEISHU_BITABLE_APP_TOKEN") or values.get("FEISHU_BITABLE_APP_TOKEN") or "").strip()
    table_id = (os.getenv("FEISHU_BITABLE_CANDIDATE_TABLE_ID") or values.get("FEISHU_BITABLE_CANDIDATE_TABLE_ID") or "").strip()
    if not app_token or not table_id:
        return
    company = bind.execute(sa.text("SELECT id FROM companies WHERE status = 'ACTIVE' ORDER BY id LIMIT 1")).first()
    if not company:
        return
    bind.execute(
        sa.text(
            "INSERT INTO recruitment_feishu_bitable_configs "
            "(id, company_id, table_url, app_token, candidate_table_id, table_name, status, validation_json, "
            "validated_at, activated_at, created_by, created_at, updated_at) "
            "VALUES (:id, :company_id, :url, :app_token, :table_id, :name, 'ACTIVE', :validation, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": str(uuid4()),
            "company_id": company[0],
            "url": "",
            "app_token": app_token,
            "table_id": table_id,
            "name": "环境变量迁移的候选人表",
            "validation": "{}",
        },
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM recruitment_feishu_bitable_configs WHERE table_name = '环境变量迁移的候选人表'"))
