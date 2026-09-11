import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def uuid4() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class FeishuBitableConfig(Base, TimestampMixin):
    __tablename__ = "recruitment_feishu_bitable_configs"
    __table_args__ = (Index("ix_feishu_bitable_config_company_status", "company_id", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    table_url: Mapped[str] = mapped_column(String(500))
    app_token: Mapped[str] = mapped_column(String(120))
    candidate_table_id: Mapped[str] = mapped_column(String(120))
    table_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True)


class Recruiter(Base, TimestampMixin):
    __tablename__ = "recruitment_recruiters"
    __table_args__ = (
        Index(
            "uq_recruiter_company_feishu_open_id",
            "company_id",
            "feishu_open_id",
            unique=True,
            postgresql_where=text("feishu_open_id IS NOT NULL"),
            sqlite_where=text("feishu_open_id IS NOT NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    feishu_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    feishu_display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(30), default="RECRUITER")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class RecruiterAccessProfile(Base, TimestampMixin):
    __tablename__ = "recruitment_recruiter_access_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), unique=True, index=True)
    data_scope: Mapped[str] = mapped_column(String(20), default="OWN")
    can_manage_team: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_feishu: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_jobs: Mapped[bool] = mapped_column(Boolean, default=False)
    can_reset_system: Mapped[bool] = mapped_column(Boolean, default=False)


class FeishuBindingAttempt(Base):
    __tablename__ = "recruitment_feishu_binding_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    account_display_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recruiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    device_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    device_poll_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecruitmentAccount(Base, TimestampMixin):
    __tablename__ = "recruitment_accounts"
    __table_args__ = (UniqueConstraint("company_id", "platform", "platform_account_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    platform_account_key: Mapped[str] = mapped_column(String(200))
    account_display_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class BossAccountAssignment(Base, TimestampMixin):
    """Current and historical Feishu ownership of a BOSS account."""
    __tablename__ = "recruitment_boss_account_assignments"
    __table_args__ = (
        Index("uq_boss_assignment_active_account", "boss_account_id", unique=True, postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'")),
        Index("uq_boss_assignment_active_feishu", "company_id", "feishu_recruiter_id", unique=True, postgresql_where=text("status = 'ACTIVE' AND feishu_recruiter_id IS NOT NULL"), sqlite_where=text("status = 'ACTIVE' AND feishu_recruiter_id IS NOT NULL")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    boss_account_id: Mapped[str] = mapped_column(ForeignKey("recruitment_accounts.id"), index=True)
    feishu_recruiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True, index=True)
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    feishu_display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    assignment_version: Mapped[int] = mapped_column(Integer, default=1)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    unassigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)


class RecruitmentJob(Base, TimestampMixin):
    __tablename__ = "recruitment_jobs"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    canonical_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class JobAlias(Base, TimestampMixin):
    __tablename__ = "recruitment_job_aliases"
    __table_args__ = (UniqueConstraint("company_id", "platform", "normalized_alias"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("recruitment_jobs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    raw_alias: Mapped[str] = mapped_column(String(120))
    normalized_alias: Mapped[str] = mapped_column(String(120))


class UnmappedJob(Base, TimestampMixin):
    __tablename__ = "recruitment_unmapped_jobs"
    __table_args__ = (UniqueConstraint("company_id", "platform", "normalized_job_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    raw_job_name: Mapped[str] = mapped_column(String(120))
    normalized_job_name: Mapped[str] = mapped_column(String(120))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    resolved_job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidateSource(Base, TimestampMixin):
    __tablename__ = "recruitment_candidate_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "source_identity_key"),
        Index("ix_candidate_match", "company_id", "candidate_normalized_name", "job_id"),
        Index("ix_candidate_platform_id", "company_id", "platform", "platform_candidate_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    platform_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_accounts.id"), nullable=True, index=True)
    source_identity_key: Mapped[str] = mapped_column(String(64), index=True)
    source_identity_type: Mapped[str] = mapped_column(String(30))
    platform_candidate_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    platform_id_scope: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    page_url_hash: Mapped[str] = mapped_column(String(64))
    candidate_display_name: Mapped[str] = mapped_column(String(120))
    candidate_normalized_name: Mapped[str] = mapped_column(String(120))
    candidate_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    candidate_experience: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    candidate_education: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    candidate_identity_signature: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    conversation_job_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    raw_job_name: Mapped[str] = mapped_column(String(120))
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    conversation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    extractor_version: Mapped[str] = mapped_column(String(80))
    feishu_record_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    recruitment_status: Mapped[str] = mapped_column(String(30), default="沟通中")
    status_evidence: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status_rule_version: Mapped[str] = mapped_column(String(20), default="boss-status-v1")
    snapshot_tokens_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    snapshot_status: Mapped[str] = mapped_column(String(20), default="NONE")
    resume_tokens_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    resume_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resume_status: Mapped[str] = mapped_column(String(30), default="NONE")
    resume_file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    data_minimized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Engagement(Base, TimestampMixin):
    __tablename__ = "recruitment_engagements"
    __table_args__ = (UniqueConstraint("candidate_source_id", "recruiter_id", "job_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True, index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    recruitment_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_accounts.id"), nullable=True)
    owner_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    stage: Mapped[str] = mapped_column(String(40), index=True)
    first_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Interview(Base, TimestampMixin):
    __tablename__ = "recruitment_interviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    location_type: Mapped[str] = mapped_column(String(30), default="ONLINE")
    location_text: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="SCHEDULED")
    result: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RecruitmentEvent(Base):
    __tablename__ = "recruitment_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source: Mapped[str] = mapped_column(String(30), default="PLUGIN")
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BossDailyMetric(Base, TimestampMixin):
    """Daily counters read from the BOSS company recruitment dashboard."""
    __tablename__ = "recruitment_boss_daily_metrics"
    __table_args__ = (UniqueConstraint("company_id", "boss_account_id", "metric_date"), Index("ix_boss_daily_metric_date", "company_id", "metric_date"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    boss_account_id: Mapped[str] = mapped_column(ForeignKey("recruitment_accounts.id"), index=True)
    metric_date: Mapped[str] = mapped_column(String(10))
    boss_name: Mapped[str] = mapped_column(String(100))
    boss_viewed_talent: Mapped[int] = mapped_column(Integer, default=0)
    boss_started_chat: Mapped[int] = mapped_column(Integer, default=0)
    boss_communication: Mapped[int] = mapped_column(Integer, default=0)
    talent_viewed_boss: Mapped[int] = mapped_column(Integer, default=0)
    talent_started_chat: Mapped[int] = mapped_column(Integer, default=0)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BossCompanyDailyConfig(Base, TimestampMixin):
    __tablename__ = "boss_company_daily_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), unique=True)
    collector_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_accounts.id"), nullable=True)
    collector_name: Mapped[str] = mapped_column(String(100))
    app_token: Mapped[str] = mapped_column(String(120))
    table_id: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_collected_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    last_collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BossCompanyDailyRow(Base, TimestampMixin):
    __tablename__ = "boss_company_daily_rows"
    __table_args__ = (UniqueConstraint("company_id", "metric_date", "boss_name"), Index("ix_company_daily_due", "status", "next_retry_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    collector_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_accounts.id"), nullable=True)
    metric_date: Mapped[str] = mapped_column(String(10))
    boss_name: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    feishu_record_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_error: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)


class Conflict(Base, TimestampMixin):
    __tablename__ = "recruitment_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    left_candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"))
    right_candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"))
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    left_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    right_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    match_level: Mapped[str] = mapped_column(String(40))
    match_reason: Mapped[str] = mapped_column(String(300))
    conflict_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="OPEN")
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True)


class ConflictExclusion(Base):
    __tablename__ = "recruitment_conflict_exclusions"
    __table_args__ = (UniqueConstraint("company_id", "left_candidate_source_id", "right_candidate_source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    left_candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"))
    right_candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationOutbox(Base, TimestampMixin):
    __tablename__ = "recruitment_notification_outbox"
    __table_args__ = (Index("ix_outbox_due", "status", "next_retry_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    event_type: Mapped[str] = mapped_column(String(60))
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    recipient_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class DuplicateLookupAlert(Base, TimestampMixin):
    __tablename__ = "recruitment_duplicate_lookup_alerts"
    __table_args__ = (
        UniqueConstraint("alert_key"),
        Index("ix_lookup_alert_company_detected", "company_id", "last_detected_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    viewer_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    matched_recruiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True, index=True)
    matched_recruiter_name: Mapped[str] = mapped_column(String(100))
    candidate_identity_hash: Mapped[str] = mapped_column(String(64))
    normalized_job_name: Mapped[str] = mapped_column(String(120))
    alert_key: Mapped[str] = mapped_column(String(64))
    match_level: Mapped[str] = mapped_column(String(40))
    evidence_rank: Mapped[int] = mapped_column(Integer)
    match_reason: Mapped[str] = mapped_column(String(300))
    notification_version: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidateSyncOutbox(Base, TimestampMixin):
    __tablename__ = "recruitment_candidate_sync_outbox"
    __table_args__ = (UniqueConstraint("candidate_source_id"), Index("ix_candidate_sync_due", "status", "next_retry_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class ConversationScanCheckpoint(Base, TimestampMixin):
    __tablename__ = "recruitment_conversation_scan_checkpoints"
    __table_args__ = (UniqueConstraint("company_id", "platform", "account_display_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30), default="boss")
    account_display_name: Mapped[str] = mapped_column(String(100))
    completed_through_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Notification(Base, TimestampMixin):
    __tablename__ = "recruitment_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    outbox_id: Mapped[str] = mapped_column(ForeignKey("recruitment_notification_outbox.id"), unique=True)
    recipient_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    provider: Mapped[str] = mapped_column(String(30))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PluginDevice(Base, TimestampMixin):
    __tablename__ = "recruitment_plugin_devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    device_id: Mapped[str] = mapped_column(String(100), unique=True)
    device_name: Mapped[str] = mapped_column(String(100))
    refresh_token_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "recruitment_worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    worker_name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="HEALTHY")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    total_processed: Mapped[int] = mapped_column(Integer, default=0)


class RecruitmentSetting(Base, TimestampMixin):
    __tablename__ = "recruitment_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), unique=True)
    notify_on_contact: Mapped[bool] = mapped_column(Boolean, default=True)
    catchup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reset_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_generation: Mapped[int] = mapped_column(Integer, default=0)


class SystemResetJob(Base, TimestampMixin):
    __tablename__ = "recruitment_system_reset_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), unique=True, index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    preview_version: Mapped[str] = mapped_column(String(64))
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PluginDiagnostic(Base):
    __tablename__ = "recruitment_plugin_diagnostics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    platform: Mapped[str] = mapped_column(String(30))
    adapter_version: Mapped[str] = mapped_column(String(80))
    page_type: Mapped[str] = mapped_column(String(50))
    account_status: Mapped[str] = mapped_column(String(30))
    candidate_status: Mapped[str] = mapped_column(String(30))
    job_status: Mapped[str] = mapped_column(String(30))
    platform_id_status: Mapped[str] = mapped_column(String(30))
    error_codes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    sanitized_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "recruitment_audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MockFeishuMessage(Base):
    __tablename__ = "mock_feishu_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    recipient_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="SENT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
