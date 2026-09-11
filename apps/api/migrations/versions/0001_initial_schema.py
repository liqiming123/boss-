"""Create the schema that existed at the initial release.

Keep this revision as a frozen historical snapshot. Importing the current ORM
metadata here makes later tables appear in 0001 and then be created twice.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "companies", _id(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False), *_timestamps(),
    )
    op.create_table(
        "recruitment_jobs", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("canonical_name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), *_timestamps(),
        sa.UniqueConstraint("company_id", "code"),
    )
    op.create_index(op.f("ix_recruitment_jobs_company_id"), "recruitment_jobs", ["company_id"])
    op.create_table(
        "recruitment_recruiters", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("feishu_open_id", sa.String(100), nullable=True),
        sa.Column("feishu_user_id", sa.String(100), nullable=True),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), *_timestamps(),
    )
    op.create_index(op.f("ix_recruitment_recruiters_company_id"), "recruitment_recruiters", ["company_id"])
    op.create_table(
        "recruitment_settings", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False, unique=True),
        sa.Column("notify_on_view", sa.Boolean(), nullable=False),
        sa.Column("notify_on_contact", sa.Boolean(), nullable=False),
        sa.Column("renotify_interval_hours", sa.Integer(), nullable=False),
        sa.Column("allow_company_level_rejection_roles", sa.JSON(), nullable=False), *_timestamps(),
    )
    op.create_table(
        "mock_feishu_messages", _id(),
        sa.Column("recipient_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recruitment_accounts", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("platform_account_key", sa.String(200), nullable=False),
        sa.Column("account_display_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True), *_timestamps(),
        sa.UniqueConstraint("company_id", "platform", "platform_account_key"),
    )
    op.create_index(op.f("ix_recruitment_accounts_company_id"), "recruitment_accounts", ["company_id"])
    op.create_index(op.f("ix_recruitment_accounts_recruiter_id"), "recruitment_accounts", ["recruiter_id"])
    op.create_table(
        "recruitment_audit_logs", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=False),
        sa.Column("after_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_recruitment_audit_logs_company_id"), "recruitment_audit_logs", ["company_id"])
    op.create_table(
        "recruitment_device_codes", _id(),
        sa.Column("device_code_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("user_code", sa.String(12), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True), *_timestamps(),
    )
    op.create_table(
        "recruitment_job_aliases", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("raw_alias", sa.String(120), nullable=False),
        sa.Column("normalized_alias", sa.String(120), nullable=False), *_timestamps(),
        sa.UniqueConstraint("company_id", "platform", "normalized_alias"),
    )
    op.create_index(op.f("ix_recruitment_job_aliases_company_id"), "recruitment_job_aliases", ["company_id"])
    op.create_index(op.f("ix_recruitment_job_aliases_job_id"), "recruitment_job_aliases", ["job_id"])
    op.create_table(
        "recruitment_notification_outbox", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("recipient_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True), *_timestamps(),
    )
    op.create_index("ix_outbox_due", "recruitment_notification_outbox", ["status", "next_retry_at"])
    op.create_table(
        "recruitment_plugin_devices", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("device_id", sa.String(100), nullable=False, unique=True),
        sa.Column("device_name", sa.String(100), nullable=False),
        sa.Column("refresh_token_hash", sa.String(128), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True), *_timestamps(),
    )
    op.create_table(
        "recruitment_plugin_diagnostics", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("adapter_version", sa.String(80), nullable=False),
        sa.Column("page_type", sa.String(50), nullable=False),
        sa.Column("account_status", sa.String(30), nullable=False),
        sa.Column("candidate_status", sa.String(30), nullable=False),
        sa.Column("job_status", sa.String(30), nullable=False),
        sa.Column("platform_id_status", sa.String(30), nullable=False),
        sa.Column("error_codes_json", sa.JSON(), nullable=False),
        sa.Column("sanitized_context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recruitment_unmapped_jobs", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("raw_job_name", sa.String(120), nullable=False),
        sa.Column("normalized_job_name", sa.String(120), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("resolved_job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True), *_timestamps(),
        sa.UniqueConstraint("company_id", "platform", "normalized_job_name"),
    )
    op.create_index(op.f("ix_recruitment_unmapped_jobs_company_id"), "recruitment_unmapped_jobs", ["company_id"])
    op.create_table(
        "recruitment_candidate_sources", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("platform_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id"), nullable=False),
        sa.Column("source_identity_key", sa.String(64), nullable=False),
        sa.Column("source_identity_type", sa.String(30), nullable=False),
        sa.Column("platform_candidate_id", sa.String(200), nullable=True),
        sa.Column("platform_id_scope", sa.String(20), nullable=False),
        sa.Column("page_url_hash", sa.String(64), nullable=False),
        sa.Column("candidate_display_name", sa.String(120), nullable=False),
        sa.Column("candidate_normalized_name", sa.String(120), nullable=False),
        sa.Column("raw_job_name", sa.String(120), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extractor_version", sa.String(80), nullable=False), *_timestamps(),
        sa.UniqueConstraint("company_id", "source_identity_key"),
    )
    op.create_index("ix_candidate_match", "recruitment_candidate_sources", ["company_id", "candidate_normalized_name", "job_id"])
    op.create_index("ix_candidate_platform_id", "recruitment_candidate_sources", ["company_id", "platform", "platform_candidate_id"])
    op.create_index(op.f("ix_recruitment_candidate_sources_company_id"), "recruitment_candidate_sources", ["company_id"])
    op.create_index(op.f("ix_recruitment_candidate_sources_platform_account_id"), "recruitment_candidate_sources", ["platform_account_id"])
    op.create_index(op.f("ix_recruitment_candidate_sources_source_identity_key"), "recruitment_candidate_sources", ["source_identity_key"])
    _create_candidate_dependents()


def _create_candidate_dependents() -> None:
    op.create_table(
        "recruitment_notifications", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("outbox_id", sa.String(36), sa.ForeignKey("recruitment_notification_outbox.id"), nullable=False, unique=True),
        sa.Column("recipient_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_message_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True), *_timestamps(),
    )
    op.create_table(
        "recruitment_conflict_exclusions", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("left_candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("right_candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "left_candidate_source_id", "right_candidate_source_id"),
    )
    op.create_table(
        "recruitment_conflicts", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("left_candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("right_candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("left_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("right_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("match_level", sa.String(40), nullable=False),
        sa.Column("match_reason", sa.String(300), nullable=False),
        sa.Column("conflict_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("notification_version", sa.Integer(), nullable=False),
        sa.Column("last_notified_version", sa.Integer(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=True), *_timestamps(),
    )
    op.create_index(op.f("ix_recruitment_conflicts_company_id"), "recruitment_conflicts", ["company_id"])
    op.create_index(op.f("ix_recruitment_conflicts_conflict_key"), "recruitment_conflicts", ["conflict_key"], unique=True)
    op.create_table(
        "recruitment_engagements", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("recruitment_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id"), nullable=False),
        sa.Column("owner_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("first_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_scope", sa.String(30), nullable=True),
        sa.Column("rejection_reason_code", sa.String(80), nullable=True),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False), *_timestamps(),
        sa.UniqueConstraint("candidate_source_id", "recruiter_id", "job_id"),
    )
    for column in ("candidate_source_id", "company_id", "job_id", "recruiter_id", "stage"):
        op.create_index(op.f(f"ix_recruitment_engagements_{column}"), "recruitment_engagements", [column])
    op.create_table(
        "recruitment_events", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("candidate_source_id", "company_id", "recruiter_id"):
        op.create_index(op.f(f"ix_recruitment_events_{column}"), "recruitment_events", [column])
    op.create_table(
        "recruitment_interviews", _id(),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("candidate_source_id", sa.String(36), sa.ForeignKey("recruitment_candidate_sources.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("recruitment_jobs.id"), nullable=True),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("location_type", sa.String(30), nullable=False),
        sa.Column("location_text", sa.String(300), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True), *_timestamps(),
    )


def downgrade() -> None:
    for table_name in (
        "recruitment_interviews", "recruitment_events", "recruitment_engagements",
        "recruitment_conflicts", "recruitment_conflict_exclusions", "recruitment_notifications",
        "recruitment_candidate_sources", "recruitment_unmapped_jobs", "recruitment_plugin_diagnostics",
        "recruitment_plugin_devices", "recruitment_notification_outbox", "recruitment_job_aliases",
        "recruitment_device_codes", "recruitment_audit_logs", "recruitment_accounts",
        "mock_feishu_messages", "recruitment_settings", "recruitment_recruiters",
        "recruitment_jobs", "companies",
    ):
        op.drop_table(table_name)
