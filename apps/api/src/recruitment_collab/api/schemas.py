from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class DevLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class ContextResolveRequest(BaseModel):
    platform: str = Field(pattern=r"^[a-z0-9_-]{2,30}$")
    page_url: HttpUrl
    platform_candidate_id: Optional[str] = Field(default=None, max_length=200)
    platform_id_scope: str = "UNKNOWN"
    candidate_display_name: str = Field(min_length=1, max_length=120)
    job_display_name: str = Field(min_length=1, max_length=120)
    account_display_name: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    client_event_id: str = Field(min_length=8, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=80)


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
    error_codes: list[str] = []
    sanitized_context: dict[str, Any] = {}


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


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

