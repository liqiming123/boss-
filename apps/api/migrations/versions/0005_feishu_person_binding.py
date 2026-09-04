"""Add verified Feishu person bindings.

Revision ID: 0005_feishu_binding
Revises: 0004_candidate_sync_outbox
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_feishu_binding"
down_revision = "0004_candidate_sync_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    recruiter_columns = {item["name"] for item in inspector.get_columns("recruitment_recruiters")}
    if "feishu_display_name" not in recruiter_columns:
        with op.batch_alter_table("recruitment_recruiters") as batch:
            batch.add_column(sa.Column("feishu_display_name", sa.String(length=100), nullable=True))
    if "recruitment_feishu_binding_attempts" in inspector.get_table_names():
        return
    op.create_table(
        "recruitment_feishu_binding_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("account_display_name", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(op.f("ix_recruitment_feishu_binding_attempts_company_id"), "recruitment_feishu_binding_attempts", ["company_id"])
    op.create_index(op.f("ix_recruitment_feishu_binding_attempts_state_hash"), "recruitment_feishu_binding_attempts", ["state_hash"])


def downgrade() -> None:
    op.drop_index(op.f("ix_recruitment_feishu_binding_attempts_state_hash"), table_name="recruitment_feishu_binding_attempts")
    op.drop_index(op.f("ix_recruitment_feishu_binding_attempts_company_id"), table_name="recruitment_feishu_binding_attempts")
    op.drop_table("recruitment_feishu_binding_attempts")
    with op.batch_alter_table("recruitment_recruiters") as batch:
        batch.drop_column("feishu_display_name")
