"""Remove columns duplicated by timestamps or superseded workflows.

Revision ID: 0013_remove_redundant_columns
Revises: 0012_remove_unused_settings
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_remove_redundant_columns"
down_revision = "0012_remove_unused_settings"
branch_labels = None
depends_on = None

REMOVED = {
    "recruitment_accounts": ("last_verified_at",),
    "recruitment_unmapped_jobs": ("first_seen_at",),
    "recruitment_candidate_sources": (
        "first_seen_at",
        "resume_tokens_json",
        "resume_hash",
        "resume_status",
        "resume_file_name",
    ),
    "recruitment_engagements": ("next_follow_up_at", "rejection_scope", "rejection_reason_code", "rejection_note"),
    "recruitment_conflicts": ("notification_version", "last_notified_version", "first_detected_at"),
    "recruitment_notification_outbox": ("channel",),
    "recruitment_duplicate_lookup_alerts": ("first_detected_at",),
    "recruitment_notifications": ("error_code", "error_message"),
    "recruitment_audit_logs": ("before_json", "request_id"),
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, names in REMOVED.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for name in names:
                if name in existing:
                    batch.drop_column(name)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    definitions = {
        "recruitment_accounts": (sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),),
        "recruitment_unmapped_jobs": (sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),),
        "recruitment_candidate_sources": (
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resume_tokens_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("resume_hash", sa.String(length=64), nullable=True),
            sa.Column("resume_status", sa.String(length=30), nullable=False, server_default="NONE"),
            sa.Column("resume_file_name", sa.String(length=255), nullable=True),
        ),
        "recruitment_engagements": (
            sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_scope", sa.String(length=30), nullable=True),
            sa.Column("rejection_reason_code", sa.String(length=80), nullable=True),
            sa.Column("rejection_note", sa.Text(), nullable=True),
        ),
        "recruitment_conflicts": (
            sa.Column("notification_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_notified_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=True),
        ),
        "recruitment_notification_outbox": (sa.Column("channel", sa.String(length=20), nullable=False, server_default="FEISHU"),),
        "recruitment_duplicate_lookup_alerts": (sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=True),),
        "recruitment_notifications": (
            sa.Column("error_code", sa.String(length=50), nullable=True),
            sa.Column("error_message", sa.String(length=500), nullable=True),
        ),
        "recruitment_audit_logs": (
            sa.Column("before_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("request_id", sa.String(length=36), nullable=True),
        ),
    }
    for table, columns in definitions.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column.name not in existing:
                    batch.add_column(column)
