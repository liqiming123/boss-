"""Store the daily counters shown in the BOSS company dashboard."""
import sqlalchemy as sa
from alembic import op

revision = "0027_boss_daily_metrics"
down_revision = "0026_boss_account_assignments"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "recruitment_boss_daily_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("boss_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id"), nullable=False),
        sa.Column("metric_date", sa.String(10), nullable=False),
        sa.Column("boss_name", sa.String(100), nullable=False),
        sa.Column("boss_viewed_talent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("boss_started_chat", sa.Integer, nullable=False, server_default="0"),
        sa.Column("boss_communication", sa.Integer, nullable=False, server_default="0"),
        sa.Column("talent_viewed_boss", sa.Integer, nullable=False, server_default="0"),
        sa.Column("talent_started_chat", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "boss_account_id", "metric_date"),
    )
    op.create_index("ix_boss_daily_metric_date", "recruitment_boss_daily_metrics", ["company_id", "metric_date"])

def downgrade() -> None:
    op.drop_index("ix_boss_daily_metric_date", table_name="recruitment_boss_daily_metrics")
    op.drop_table("recruitment_boss_daily_metrics")
