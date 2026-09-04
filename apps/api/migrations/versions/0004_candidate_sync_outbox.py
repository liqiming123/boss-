"""Queue candidate rows for asynchronous Feishu synchronization.

Revision ID: 0004_candidate_sync_outbox
Revises: 0003_conversation_times
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_candidate_sync_outbox"
down_revision = "0003_conversation_times"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("recruitment_candidate_sources")}
    if "feishu_record_id" not in columns:
        with op.batch_alter_table("recruitment_candidate_sources") as batch:
            batch.add_column(sa.Column("feishu_record_id", sa.String(length=100), nullable=True))
    if "recruitment_candidate_sync_outbox" in inspector.get_table_names():
        return
    op.create_table(
        "recruitment_candidate_sync_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_source_id", sa.String(length=36), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_source_id"], ["recruitment_candidate_sources.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_source_id"),
    )
    op.create_index("ix_candidate_sync_due", "recruitment_candidate_sync_outbox", ["status", "next_retry_at"])
    op.create_index(op.f("ix_recruitment_candidate_sync_outbox_candidate_source_id"), "recruitment_candidate_sync_outbox", ["candidate_source_id"])
    op.create_index(op.f("ix_recruitment_candidate_sync_outbox_company_id"), "recruitment_candidate_sync_outbox", ["company_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_recruitment_candidate_sync_outbox_company_id"), table_name="recruitment_candidate_sync_outbox")
    op.drop_index(op.f("ix_recruitment_candidate_sync_outbox_candidate_source_id"), table_name="recruitment_candidate_sync_outbox")
    op.drop_index("ix_candidate_sync_due", table_name="recruitment_candidate_sync_outbox")
    op.drop_table("recruitment_candidate_sync_outbox")
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.drop_column("feishu_record_id")
