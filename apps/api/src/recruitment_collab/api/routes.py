from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recruitment_collab.application.collaboration import ApplicationError, RecruitmentCollaborationService
from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.normalization import JobNameNormalizer
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.feishu import FeishuCallbackVerifier
from recruitment_collab.infrastructure.models import (
    AuditLog,
    CandidateSource,
    Conflict,
    DeviceCode,
    Engagement,
    Interview,
    JobAlias,
    MockFeishuMessage,
    NotificationOutbox,
    PluginDevice,
    PluginDiagnostic,
    Recruiter,
    RecruitmentAccount,
    RecruitmentEvent,
    RecruitmentJob,
    RecruitmentSetting,
    UnmappedJob,
    now,
)
from recruitment_collab.infrastructure.security import decode_token, hash_password, hash_token, make_token, verify_password

from .dependencies import Actor, current_actor, require_admin
from .schemas import (
    AccountCreate,
    AliasCreate,
    ContextResolveRequest,
    DevLoginRequest,
    DiagnosticRequest,
    EventRequest,
    InterviewRequest,
    JobCreate,
    ReasonRequest,
    RecruiterCreate,
)

router = APIRouter(prefix="/api/v1")


def serialize(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ready"}


@router.post("/dev/login")
def dev_login(body: DevLoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not get_settings().is_development:
        raise ApplicationError("DEV_LOGIN_DISABLED", "开发登录未开启", 404)
    user = db.scalar(select(Recruiter).where(Recruiter.email == body.email))
    if not user or not verify_password(user.password_hash, body.password):
        raise ApplicationError("INVALID_CREDENTIALS", "邮箱或密码错误", 401)
    return {"access_token": make_token(user.id, user.company_id, user.role), "refresh_token": make_token(user.id, user.company_id, user.role, "refresh"), "token_type": "bearer", "user": {"id": user.id, "display_name": user.display_name, "role": user.role}}


@router.post("/auth/refresh")
def refresh(body: dict[str, str], db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        payload = decode_token(body.get("refresh_token", ""), "refresh")
    except Exception as exc:
        raise ApplicationError("INVALID_REFRESH_TOKEN", "刷新令牌无效", 401) from exc
    user = db.get(Recruiter, payload["sub"])
    if not user or user.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "账号不可用", 401)
    return {"access_token": make_token(user.id, user.company_id, user.role), "token_type": "bearer"}


@router.post("/auth/logout")
def logout(_: Actor = Depends(current_actor)) -> dict[str, bool]:
    return {"success": True}


@router.get("/auth/me")
@router.get("/plugin/me")
def me(actor: Actor = Depends(current_actor)) -> dict[str, str]:
    return actor.__dict__


@router.post("/auth/device/start")
def device_start(body: dict[str, str], db: Session = Depends(get_db)) -> dict[str, Any]:
    raw = secrets.token_urlsafe(32)
    user_code = secrets.token_hex(4).upper()
    code = DeviceCode(device_code_hash=hash_token(raw), user_code=user_code, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    db.add(code); db.commit()
    return {"device_code": raw, "user_code": user_code, "verification_uri": "http://localhost:5173/device", "expires_in": 600, "interval": 3}


@router.post("/auth/device/poll")
def device_poll(body: dict[str, str], db: Session = Depends(get_db)) -> dict[str, Any]:
    code = db.scalar(select(DeviceCode).where(DeviceCode.device_code_hash == hash_token(body.get("device_code", ""))))
    if not code or code.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise ApplicationError("DEVICE_CODE_EXPIRED", "设备码已过期", 400)
    if code.status != "APPROVED" or not code.recruiter_id:
        return {"status": "PENDING"}
    user = db.get(Recruiter, code.recruiter_id)
    if not user or user.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "绑定账号不可用", 401)
    refresh_token = make_token(user.id, user.company_id, user.role, "refresh")
    device = PluginDevice(company_id=user.company_id, recruiter_id=user.id, device_id=body.get("device_id", secrets.token_hex(12)), device_name=body.get("device_name", "Chrome"), refresh_token_hash=hash_token(refresh_token))
    db.add(device); code.status = "CONSUMED"; db.commit()
    return {"status": "APPROVED", "access_token": make_token(user.id, user.company_id, user.role), "refresh_token": refresh_token}


@router.post("/auth/device/approve")
def device_approve(body: dict[str, str], actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    code = db.scalar(select(DeviceCode).where(DeviceCode.user_code == body.get("user_code"), DeviceCode.status == "PENDING"))
    if not code:
        raise ApplicationError("DEVICE_CODE_NOT_FOUND", "设备码无效", 404)
    code.status, code.recruiter_id, code.approved_at = "APPROVED", actor.id, now(); db.commit()
    return {"success": True}


@router.post("/auth/devices/{device_id}/revoke")
def revoke_device(device_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    device = db.get(PluginDevice, device_id)
    if not device or (device.recruiter_id != actor.id and actor.role not in {"ADMIN", "HR_MANAGER"}):
        raise ApplicationError("DEVICE_NOT_FOUND", "设备不存在", 404)
    device.status, device.revoked_at = "REVOKED", now(); db.commit(); return {"success": True}


@router.get("/auth/feishu/login")
def feishu_login() -> dict[str, str]:
    if get_settings().feishu_mode == "mock":
        return {"mode": "mock", "message": "Mock 模式请使用开发登录"}
    return {"mode": "real", "authorization_url": "https://open.feishu.cn/open-apis/authen/v1/authorize"}


@router.get("/auth/feishu/callback")
def feishu_callback(code: str = Query(...)) -> dict[str, str]:
    return {"status": "received", "code_hash": hashlib.sha256(code.encode()).hexdigest()}


@router.post("/plugin/context/resolve")
def resolve_context(body: ContextResolveRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return RecruitmentCollaborationService(db).resolve_context(actor.id, actor.company_id, {**body.model_dump(mode="json"), "page_url": str(body.page_url)})


@router.post("/plugin/engagements/claim")
def claim(body: EventRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    data = body.model_dump(); data["event_type"] = "CLAIMED"
    return RecruitmentCollaborationService(db).record_event(actor.id, actor.company_id, data)


@router.post("/plugin/events")
def event(body: EventRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return RecruitmentCollaborationService(db).record_event(actor.id, actor.company_id, body.model_dump())


@router.post("/plugin/interviews")
def interview(body: InterviewRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(CandidateSource, body.candidate_source_id)
    if not source or source.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    row = Interview(company_id=actor.company_id, candidate_source_id=source.id, job_id=source.job_id, recruiter_id=actor.id, scheduled_at=body.scheduled_at, duration_minutes=body.duration_minutes, location_type=body.location_type, location_text=body.location_text, notes=body.notes)
    db.add(row); db.flush()
    RecruitmentCollaborationService(db).record_event(actor.id, actor.company_id, {"candidate_source_id": source.id, "event_type": "INTERVIEW_INVITED", "idempotency_key": body.idempotency_key, "reason": None})
    return serialize(row)


@router.get("/plugin/candidates/{source_id}/timeline")
def timeline(source_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    source = db.get(CandidateSource, source_id)
    if not source or source.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    return [serialize(row) for row in db.scalars(select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id).order_by(RecruitmentEvent.event_time.desc())).all()]


@router.post("/plugin/diagnostics")
def diagnostics(body: DiagnosticRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, str]:
    forbidden = {"html", "cookie", "token", "password"}
    clean = {key: value for key, value in body.sanitized_context.items() if key.lower() not in forbidden}
    row = PluginDiagnostic(company_id=actor.company_id, recruiter_id=actor.id, platform=body.platform, adapter_version=body.adapter_version, page_type=body.page_type, account_status=body.account_status, candidate_status=body.candidate_status, job_status=body.job_status, platform_id_status=body.platform_id_status, error_codes_json=body.error_codes, sanitized_context_json=clean)
    db.add(row); db.commit(); return {"id": row.id}


@router.get("/conflicts")
@router.get("/admin/conflicts")
def conflicts(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [serialize(row) for row in db.scalars(select(Conflict).where(Conflict.company_id == actor.company_id).order_by(Conflict.created_at.desc())).all()]


@router.get("/conflicts/{conflict_id}")
@router.get("/admin/conflicts/{conflict_id}")
def conflict_detail(conflict_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id: raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    return serialize(row)


@router.post("/conflicts/{conflict_id}/exclude")
def exclude(conflict_id: str, body: ReasonRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    RecruitmentCollaborationService(db).exclude_conflict(conflict_id, actor.id, actor.company_id, body.reason); return {"success": True}


@router.post("/conflicts/{conflict_id}/{action}")
def conflict_action(conflict_id: str, action: str, body: ReasonRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    mapping = {"acknowledge": "ACKNOWLEDGED", "request-transfer": "TRANSFER_REQUESTED", "transfer": "TRANSFERRED", "close": "CLOSED", "continue": "OPEN"}
    if action not in mapping: raise ApplicationError("ACTION_NOT_SUPPORTED", "不支持的操作", 404)
    if action == "continue" and not body.reason.strip(): raise ApplicationError("REASON_REQUIRED", "继续沟通必须填写原因")
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id: raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    if action == "transfer" and actor.role not in {"ADMIN", "HR_MANAGER"}: raise ApplicationError("FORBIDDEN", "仅管理员可执行转交", 403)
    row.status = mapping[action]; row.resolution = body.reason; db.commit(); return {"success": True}


@router.get("/admin/overview")
def overview(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    def count(model: Any, *conditions: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)
    return {"candidate_queries": count(CandidateSource, CandidateSource.company_id == actor.company_id), "engagements": count(Engagement, Engagement.company_id == actor.company_id), "open_conflicts": count(Conflict, Conflict.company_id == actor.company_id, Conflict.status == "OPEN"), "interviews": count(Interview, Interview.company_id == actor.company_id, Interview.status == "SCHEDULED"), "unmapped_jobs": count(UnmappedJob, UnmappedJob.company_id == actor.company_id, UnmappedJob.resolved_job_id.is_(None)), "notification_failures": count(NotificationOutbox, NotificationOutbox.company_id == actor.company_id, NotificationOutbox.status == "FAILED")}


@router.get("/admin/{resource}")
def admin_list(resource: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    models = {"recruiters": Recruiter, "accounts": RecruitmentAccount, "jobs": RecruitmentJob, "job-aliases": JobAlias, "unmapped-jobs": UnmappedJob, "candidate-sources": CandidateSource, "interviews": Interview, "notifications": NotificationOutbox, "audit-logs": AuditLog, "plugin-diagnostics": PluginDiagnostic, "mock-feishu/messages": MockFeishuMessage}
    model = models.get(resource)
    if not model: raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在", 404)
    statement = select(model)
    if hasattr(model, "company_id"):
        statement = statement.where(model.company_id == actor.company_id)
    rows = db.scalars(statement.limit(500)).all()
    output = [serialize(row) for row in rows]
    for item in output: item.pop("password_hash", None); item.pop("refresh_token_hash", None)
    return output


@router.get("/admin/mock-feishu/messages")
def mock_feishu_messages(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    recruiter_ids = select(Recruiter.id).where(Recruiter.company_id == actor.company_id)
    rows = db.scalars(select(MockFeishuMessage).where(MockFeishuMessage.recipient_recruiter_id.in_(recruiter_ids)).order_by(MockFeishuMessage.created_at.desc())).all()
    return [serialize(row) for row in rows]


@router.get("/admin/candidate-sources/{source_id}")
def candidate_source_detail(source_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(CandidateSource, source_id)
    if not row or row.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    data = serialize(row)
    data["events"] = [serialize(item) for item in db.scalars(select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id).order_by(RecruitmentEvent.event_time.desc())).all()]
    data["interviews"] = [serialize(item) for item in db.scalars(select(Interview).where(Interview.candidate_source_id == source_id).order_by(Interview.scheduled_at.desc())).all()]
    return data


@router.patch("/admin/unmapped-jobs/{unmapped_id}")
def map_unmapped_job(unmapped_id: str, body: dict[str, str], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(UnmappedJob, unmapped_id)
    job = db.get(RecruitmentJob, body.get("job_id", ""))
    if not row or row.company_id != actor.company_id or not job or job.company_id != actor.company_id:
        raise ApplicationError("MAPPING_TARGET_NOT_FOUND", "待映射岗位或目标岗位不存在", 404)
    row.resolved_job_id, row.resolved_at = job.id, now()
    existing = db.scalar(select(JobAlias).where(JobAlias.company_id == actor.company_id, JobAlias.platform == row.platform, JobAlias.normalized_alias == row.normalized_job_name))
    if not existing:
        db.add(JobAlias(company_id=actor.company_id, job_id=job.id, platform=row.platform, raw_alias=row.raw_job_name, normalized_alias=row.normalized_job_name))
    db.commit()
    return serialize(row)


@router.post("/admin/jobs")
def create_job(body: JobCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = RecruitmentJob(company_id=actor.company_id, **body.model_dump()); db.add(row); db.commit(); return serialize(row)


@router.post("/admin/job-aliases")
def create_alias(body: AliasCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = JobAlias(company_id=actor.company_id, job_id=body.job_id, platform=body.platform, raw_alias=body.raw_alias, normalized_alias=JobNameNormalizer().normalize(body.raw_alias)); db.add(row); db.commit(); return serialize(row)


@router.post("/admin/recruiters")
def create_recruiter(body: RecruiterCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.role not in {"ADMIN", "HR_MANAGER", "RECRUITER"}: raise ApplicationError("INVALID_ROLE", "角色无效")
    row = Recruiter(company_id=actor.company_id, display_name=body.display_name, email=body.email, role=body.role, password_hash=hash_password(body.password)); db.add(row); db.commit(); data = serialize(row); data.pop("password_hash"); return data


@router.post("/admin/accounts")
def create_account(body: AccountCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = RecruitmentAccount(company_id=actor.company_id, **body.model_dump()); db.add(row); db.commit(); return serialize(row)


@router.post("/admin/notifications/{notification_id}/{action}")
def notification_action(notification_id: str, action: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.get(NotificationOutbox, notification_id)
    if not row or row.company_id != actor.company_id: raise ApplicationError("NOTIFICATION_NOT_FOUND", "通知不存在", 404)
    if action == "retry": row.status, row.retry_count, row.next_retry_at = "PENDING", 0, now()
    elif action == "cancel" and row.status in {"PENDING", "FAILED"}: row.status = "CANCELLED"
    else: raise ApplicationError("ACTION_NOT_ALLOWED", "当前状态不允许此操作")
    db.commit(); return {"success": True}


@router.get("/admin/settings")
def settings(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == actor.company_id)); return serialize(row)


@router.patch("/admin/settings")
def update_settings(body: dict[str, Any], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == actor.company_id))
    for key in {"notify_on_view", "notify_on_contact", "renotify_interval_hours"}: 
        if key in body: setattr(row, key, body[key])
    db.commit(); return serialize(row)


@router.post("/integrations/feishu/events")
@router.post("/integrations/feishu/card-callback")
def feishu_events(
    body: dict[str, Any],
    x_lark_request_timestamp: str = Header(default=""),
    x_lark_request_nonce: str = Header(default=""),
    x_lark_signature: str = Header(default=""),
) -> dict[str, Any]:
    settings = get_settings()
    if "encrypt" in body:
        verifier = FeishuCallbackVerifier(settings.feishu_encrypt_key, settings.feishu_verification_token)
        encrypted = str(body["encrypt"])
        if not verifier.verify_signature(x_lark_request_timestamp, x_lark_request_nonce, encrypted, x_lark_signature):
            raise ApplicationError("INVALID_FEISHU_SIGNATURE", "飞书回调签名无效或已过期", 401)
        body = verifier.decrypt(encrypted)
    if body.get("type") == "url_verification":
        verifier = FeishuCallbackVerifier(settings.feishu_encrypt_key, settings.feishu_verification_token)
        if not verifier.verify_token(str(body.get("token", ""))):
            raise ApplicationError("INVALID_FEISHU_TOKEN", "飞书验证令牌无效", 401)
        return {"challenge": body.get("challenge")}
    return {"ok": True}
