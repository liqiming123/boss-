"""Remove the unused pre-OAuth device-code flow.

Revision ID: 0011_remove_device_codes
Revises: 0010_lookup_alerts
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_remove_device_codes"
down_revision = "0010_lookup_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "recruitment_device_codes" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("recruitment_device_codes")


def downgrade() -> None:
    if "recruitment_device_codes" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "recruitment_device_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_code_hash", sa.String(length=128), nullable=False),
        sa.Column("user_code", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recruiter_id", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruitment_recruiters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash"),
        sa.UniqueConstraint("user_code"),
    )
