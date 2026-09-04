"""Add strict candidate identity, status, snapshot metadata and scan checkpoints.

Revision ID: 0006_identity_status_snapshot
Revises: 0005_feishu_binding
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_identity_status_snapshot"
down_revision = "0005_feishu_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("recruitment_candidate_sources")}
    additions = [
        sa.Column("candidate_age", sa.Integer(), nullable=True),
        sa.Column("candidate_experience", sa.String(length=40), nullable=True),
        sa.Column("candidate_education", sa.String(length=40), nullable=True),
        sa.Column("candidate_identity_signature", sa.String(length=64), nullable=True),
        sa.Column("conversation_job_key", sa.String(length=64), nullable=True),
        sa.Column("recruitment_status", sa.String(length=30), nullable=False, server_default="沟通中"),
        sa.Column("status_evidence", sa.String(length=200), nullable=True),
        sa.Column("status_rule_version", sa.String(length=20), nullable=False, server_default="boss-status-v1"),
        sa.Column("snapshot_tokens_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_status", sa.String(length=20), nullable=False, server_default="NONE"),
    ]
    if any(column.name not in columns for column in additions):
        with op.batch_alter_table("recruitment_candidate_sources") as batch:
            for column in additions:
                if column.name not in columns:
                    batch.add_column(column)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("recruitment_candidate_sources")}
    if "ix_recruitment_candidate_sources_candidate_identity_signature" not in indexes:
        op.create_index("ix_recruitment_candidate_sources_candidate_identity_signature", "recruitment_candidate_sources", ["candidate_identity_signature"])
    if "ix_recruitment_candidate_sources_conversation_job_key" not in indexes:
        op.create_index("ix_recruitment_candidate_sources_conversation_job_key", "recruitment_candidate_sources", ["conversation_job_key"])
    if "recruitment_conversation_scan_checkpoints" in inspector.get_table_names():
        return
    op.create_table(
        "recruitment_conversation_scan_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("account_display_name", sa.String(length=100), nullable=False),
        sa.Column("completed_through_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "platform", "account_display_name"),
    )
    op.create_index(op.f("ix_recruitment_conversation_scan_checkpoints_company_id"), "recruitment_conversation_scan_checkpoints", ["company_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_recruitment_conversation_scan_checkpoints_company_id"), table_name="recruitment_conversation_scan_checkpoints")
    op.drop_table("recruitment_conversation_scan_checkpoints")
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.drop_index("ix_recruitment_candidate_sources_conversation_job_key")
        batch.drop_index("ix_recruitment_candidate_sources_candidate_identity_signature")
        for column in (
            "snapshot_status",
            "snapshot_hash",
            "snapshot_tokens_json",
            "status_rule_version",
            "status_evidence",
            "recruitment_status",
            "conversation_job_key",
            "candidate_identity_signature",
            "candidate_education",
            "candidate_experience",
            "candidate_age",
        ):
            batch.drop_column(column)
