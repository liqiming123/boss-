from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator


class DevLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class NativeCommunicationRecord(BaseModel):
    recruiter_name: str = Field(min_length=1, max_length=100)
    job_name: str = Field(min_length=1, max_length=120)
    contacted_at: datetime
    source: str = Field(default="BOSS_NATIVE", pattern=r"^BOSS_NATIVE$")


class ContextResolveRequest(BaseModel):
    platform: str = Field(pattern=r"^[a-z0-9_-]{2,30}$")
    page_url: HttpUrl
    platform_candidate_id: Optional[str] = Field(default=None, max_length=200)
    platform_id_scope: str = "UNKNOWN"
    candidate_display_name: str = Field(min_length=1, max_length=120)
    candidate_age: Optional[int] = Field(default=None, ge=16, le=100)
    candidate_experience: Optional[str] = Field(default=None, max_length=40)
    candidate_education: Optional[str] = Field(default=None, max_length=40)
    job_display_name: str = Field(min_length=1, max_length=120)
    account_display_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    conversation_started_at: Optional[datetime] = None
    conversation_updated_at: Optional[datetime] = None
    native_communications: list[NativeCommunicationRecord] = Field(default_factory=list, max_length=100)
    observed_at: datetime
    client_event_id: str = Field(min_length=8, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=80)


class InterviewDetails(BaseModel):
    """What the plugin could read from BOSS's interview scheduler.

    Every field is optional and every validator is lenient: an unreadable or
    unexpected scheduler value is dropped instead of rejected. The interview
    schedule is a bonus, while the 已约面 status is the business fact — a bad
    date must never turn the whole message-sent request into a 422 and lose the
    invitation.
    """

    interview_type: Optional[Literal["ONLINE", "OFFLINE"]] = None
    scheduled_at: Optional[datetime] = None
    location: Optional[str] = None

    @field_validator("interview_type", mode="before")
    @classmethod
    def _known_format(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().upper()
        return text if text in {"ONLINE", "OFFLINE"} else None

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def _lenient_datetime(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @field_validator("location", mode="before")
    @classmethod
    def _bounded_location(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()[:300]
        return text or None


class MessageSentRequest(ContextResolveRequest):
    sent_at: datetime
    recruitment_status: str = Field(default="沟通中", max_length=30)
    status_evidence: Optional[str] = Field(default=None, max_length=200)
    status_rule_version: str = Field(default="boss-status-v1", max_length=20)
    resume_status: Optional[str] = Field(default=None, max_length=30)
    interview: Optional[InterviewDetails] = None


class ConversationSyncRequest(MessageSentRequest):
    has_recruiter_outbound: bool
    sync_reason: str = Field(default="CATCHUP", pattern=r"^(MESSAGE_SENT|CATCHUP|CONVERSATION_UPDATED|CANDIDATE_OPENED)$")


class ScanCheckpointRequest(BaseModel):
    platform: str = Field(default="boss", pattern=r"^[a-z0-9_-]{2,30}$")
    account_display_name: str = Field(min_length=1, max_length=100)
    completed_through_at: datetime
    cursor: dict[str, Any] = Field(default_factory=dict)


class SnapshotStatusRequest(BaseModel):
    candidate_source_ids: list[str] = Field(min_length=1, max_length=20)
    status: Literal["FAILED", "INTERRUPTED"]
    error_code: str = Field(min_length=1, max_length=100)


class FeishuBindingStartRequest(BaseModel):
    account_display_name: str = Field(min_length=1, max_length=100)
    action: str = Field(pattern=r"^(bind|unbind)$")
    device_id: Optional[str] = Field(default=None, min_length=8, max_length=100)
    device_name: Optional[str] = Field(default=None, max_length=100)


class FeishuDevicePollRequest(BaseModel):
    attempt_id: str = Field(min_length=8, max_length=100)
    poll_token: str = Field(min_length=20, max_length=200)


class EventRequest(BaseModel):
    candidate_source_id: str
    event_type: str
    idempotency_key: str = Field(min_length=8, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=500)


class InterviewRequest(BaseModel):
    candidate_source_id: str
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    location_type: str = "ONLINE"
    location_text: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=1000)
    idempotency_key: str


class DiagnosticRequest(BaseModel):
    platform: str
    adapter_version: str
    page_type: str
    account_status: str
    candidate_status: str
    job_status: str
    platform_id_status: str
    error_codes: list[str] = Field(default_factory=list)
    sanitized_context: dict[str, Any] = Field(default_factory=dict)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class RecruitmentSettingsUpdate(BaseModel):
    notify_on_contact: Optional[bool] = None
    catchup_enabled: Optional[bool] = None


class FeishuTableValidateRequest(BaseModel):
    table_url: str = Field(min_length=12, max_length=500)


class FeishuTableActivateRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


class SystemResetRequest(BaseModel):
    preview_version: str = Field(min_length=16, max_length=64)
    confirmation: str = Field(min_length=1, max_length=80)


class JobCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    canonical_name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=120)


class RecruiterCreate(BaseModel):
    display_name: str
    email: EmailStr
    role: str = "RECRUITER"
    password: str = Field(min_length=8)


class AccountCreate(BaseModel):
    recruiter_id: str
    platform: str = "boss"
    platform_account_key: str
    account_display_name: str


class BossAccountAssignmentRequest(BaseModel):
    feishu_open_id: str = Field(min_length=1, max_length=100)
    feishu_user_id: Optional[str] = Field(default=None, max_length=100)
    feishu_display_name: str = Field(min_length=1, max_length=100)
    expected_version: Optional[int] = Field(default=None, ge=1)


class AdminGrantRequest(BaseModel):
    feishu_open_id: str = Field(min_length=1, max_length=100)
    feishu_user_id: Optional[str] = Field(default=None, max_length=100)
    feishu_display_name: str = Field(min_length=1, max_length=100)


class AliasCreate(BaseModel):
    job_id: str
    platform: str = "boss"
    raw_alias: str
