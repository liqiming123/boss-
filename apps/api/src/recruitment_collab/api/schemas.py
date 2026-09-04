from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl


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


class MessageSentRequest(ContextResolveRequest):
    sent_at: datetime
    recruitment_status: str = Field(default="沟通中", max_length=30)
    status_evidence: Optional[str] = Field(default=None, max_length=200)
    status_rule_version: str = Field(default="boss-status-v1", max_length=20)
    resume_status: Optional[str] = Field(default=None, max_length=30)


class ConversationSyncRequest(MessageSentRequest):
    has_recruiter_outbound: bool
    sync_reason: str = Field(default="CATCHUP", pattern=r"^(MESSAGE_SENT|CATCHUP|CONVERSATION_UPDATED)$")


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


class AliasCreate(BaseModel):
    job_id: str
    platform: str = "boss"
    raw_alias: str
