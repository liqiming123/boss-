"""Add bounded worker heartbeat state for the developer operations console.

Revision ID: 0008_worker_heartbeats
Revises: 0007_device_identity_retention
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_worker_heartbeats"
down_revision = "0007_device_identity_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "recruitment_worker_heartbeats" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "recruitment_worker_heartbeats",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("worker_name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("total_processed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_name"),
    )
    op.create_index(op.f("ix_recruitment_worker_heartbeats_worker_name"), "recruitment_worker_heartbeats", ["worker_name"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_recruitment_worker_heartbeats_worker_name"), table_name="recruitment_worker_heartbeats")
    op.drop_table("recruitment_worker_heartbeats")
