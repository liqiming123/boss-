"""Separate company daily reporting from candidate sync and personal accounts."""
import sqlalchemy as sa
from alembic import op

revision = "0028_company_daily_sync"
down_revision = "0027_boss_daily_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("boss_company_daily_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False, unique=True),
        sa.Column("collector_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id")),
        sa.Column("collector_name", sa.String(100), nullable=False),
        sa.Column("app_token", sa.String(120), nullable=False),
        sa.Column("table_id", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_collected_date", sa.String(10)),
        sa.Column("last_collected_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("boss_company_daily_rows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("collector_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id")),
        sa.Column("metric_date", sa.String(10), nullable=False),
        sa.Column("boss_name", sa.String(100), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("feishu_record_id", sa.String(100)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(180)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "metric_date", "boss_name"))
    op.create_index("ix_company_daily_due", "boss_company_daily_rows", ["status", "next_retry_at"])


def downgrade():
    op.drop_table("boss_company_daily_rows")
    op.drop_table("boss_company_daily_configs")
