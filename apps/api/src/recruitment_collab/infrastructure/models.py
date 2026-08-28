import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class Recruiter(Base, TimestampMixin):
    __tablename__ = "recruitment_recruiters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    feishu_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(30), default="RECRUITER")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


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
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
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
    platform_account_id: Mapped[str] = mapped_column(ForeignKey("recruitment_accounts.id"), index=True)
    source_identity_key: Mapped[str] = mapped_column(String(64), index=True)
    source_identity_type: Mapped[str] = mapped_column(String(30))
    platform_candidate_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    platform_id_scope: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    page_url_hash: Mapped[str] = mapped_column(String(64))
    candidate_display_name: Mapped[str] = mapped_column(String(120))
    candidate_normalized_name: Mapped[str] = mapped_column(String(120))
    raw_job_name: Mapped[str] = mapped_column(String(120))
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    extractor_version: Mapped[str] = mapped_column(String(80))


class Engagement(Base, TimestampMixin):
    __tablename__ = "recruitment_engagements"
    __table_args__ = (UniqueConstraint("candidate_source_id", "recruiter_id", "job_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_source_id: Mapped[str] = mapped_column(ForeignKey("recruitment_candidate_sources.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_jobs.id"), nullable=True, index=True)
    recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"), index=True)
    recruitment_account_id: Mapped[str] = mapped_column(ForeignKey("recruitment_accounts.id"))
    owner_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    stage: Mapped[str] = mapped_column(String(40), index=True)
    first_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_scope: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    rejection_reason_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    rejection_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    notification_version: Mapped[int] = mapped_column(Integer, default=1)
    last_notified_version: Mapped[int] = mapped_column(Integer, default=0)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
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
    channel: Mapped[str] = mapped_column(String(20), default="FEISHU")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


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
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


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


class DeviceCode(Base, TimestampMixin):
    __tablename__ = "recruitment_device_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    device_code_hash: Mapped[str] = mapped_column(String(128), unique=True)
    user_code: Mapped[str] = mapped_column(String(12), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recruiter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recruitment_recruiters.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RecruitmentSetting(Base, TimestampMixin):
    __tablename__ = "recruitment_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), unique=True)
    notify_on_view: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_on_contact: Mapped[bool] = mapped_column(Boolean, default=True)
    renotify_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    allow_company_level_rejection_roles: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["ADMIN", "HR_MANAGER"])


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
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MockFeishuMessage(Base):
    __tablename__ = "mock_feishu_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    recipient_recruiter_id: Mapped[str] = mapped_column(ForeignKey("recruitment_recruiters.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="SENT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
