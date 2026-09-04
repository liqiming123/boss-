"""Add minimal duplicate lookup alert cooldown state.

Revision ID: 0010_lookup_alerts
Revises: 0009_resume_attachments
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_lookup_alerts"
down_revision = "0009_resume_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "recruitment_duplicate_lookup_alerts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "recruitment_duplicate_lookup_alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("viewer_recruiter_id", sa.String(length=36), nullable=False),
        sa.Column("matched_recruiter_id", sa.String(length=36), nullable=True),
        sa.Column("matched_recruiter_name", sa.String(length=100), nullable=False),
        sa.Column("candidate_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_job_name", sa.String(length=120), nullable=False),
        sa.Column("alert_key", sa.String(length=64), nullable=False),
        sa.Column("match_level", sa.String(length=40), nullable=False),
        sa.Column("evidence_rank", sa.Integer(), nullable=False),
        sa.Column("match_reason", sa.String(length=300), nullable=False),
        sa.Column("notification_version", sa.Integer(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["viewer_recruiter_id"], ["recruitment_recruiters.id"]),
        sa.ForeignKeyConstraint(["matched_recruiter_id"], ["recruitment_recruiters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_key"),
    )
    op.create_index(op.f("ix_recruitment_duplicate_lookup_alerts_company_id"), "recruitment_duplicate_lookup_alerts", ["company_id"])
    op.create_index(op.f("ix_recruitment_duplicate_lookup_alerts_viewer_recruiter_id"), "recruitment_duplicate_lookup_alerts", ["viewer_recruiter_id"])
    op.create_index(op.f("ix_recruitment_duplicate_lookup_alerts_matched_recruiter_id"), "recruitment_duplicate_lookup_alerts", ["matched_recruiter_id"])
    op.create_index("ix_lookup_alert_company_detected", "recruitment_duplicate_lookup_alerts", ["company_id", "last_detected_at"])


def downgrade() -> None:
    op.drop_table("recruitment_duplicate_lookup_alerts")
