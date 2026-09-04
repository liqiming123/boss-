"""Add Feishu device login and data minimization metadata.

Revision ID: 0007_device_identity_retention
Revises: 0006_identity_status_snapshot
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_device_identity_retention"
down_revision = "0006_identity_status_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    binding_columns = {item["name"] for item in inspector.get_columns("recruitment_feishu_binding_attempts")}
    with op.batch_alter_table("recruitment_feishu_binding_attempts") as batch:
        if "recruiter_id" not in binding_columns:
            batch.add_column(sa.Column("recruiter_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key("fk_feishu_binding_attempt_recruiter", "recruitment_recruiters", ["recruiter_id"], ["id"])
        if "device_id" not in binding_columns:
            batch.add_column(sa.Column("device_id", sa.String(length=100), nullable=True))
        if "device_name" not in binding_columns:
            batch.add_column(sa.Column("device_name", sa.String(length=100), nullable=True))
        if "device_poll_token_hash" not in binding_columns:
            batch.add_column(sa.Column("device_poll_token_hash", sa.String(length=64), nullable=True))
    candidate_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("recruitment_candidate_sources")}
    if "data_minimized_at" not in candidate_columns:
        with op.batch_alter_table("recruitment_candidate_sources") as batch:
            batch.add_column(sa.Column("data_minimized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.drop_column("data_minimized_at")
    with op.batch_alter_table("recruitment_feishu_binding_attempts") as batch:
        batch.drop_constraint("fk_feishu_binding_attempt_recruiter", type_="foreignkey")
        for column in ("device_poll_token_hash", "device_name", "device_id", "recruiter_id"):
            batch.drop_column(column)
