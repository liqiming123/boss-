from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from recruitment_collab.application.collaboration import ApplicationError, RecruitmentCollaborationService
from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.normalization import JobNameNormalizer
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.feishu import FeishuCallbackVerifier, FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import (
    AuditLog,
    CandidateSource,
    CandidateSyncOutbox,
    Conflict,
    ConversationScanCheckpoint,
    DuplicateLookupAlert,
    Engagement,
    FeishuBindingAttempt,
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
    WorkerHeartbeat,
    now,
)
from recruitment_collab.infrastructure.security import decode_token, hash_password, hash_token, make_token, verify_password

from .dependencies import ADMIN_ROLES, ADMIN_SESSION_COOKIE, Actor, bootstrap_company_id, current_actor, plugin_actor, require_admin
from .schemas import (
    AccountCreate,
    AliasCreate,
    ContextResolveRequest,
    ConversationSyncRequest,
    DevLoginRequest,
    DiagnosticRequest,
    EventRequest,
    FeishuBindingStartRequest,
    FeishuDevicePollRequest,
    InterviewRequest,
    JobCreate,
    MessageSentRequest,
    ReasonRequest,
    RecruiterCreate,
    RecruitmentSettingsUpdate,
    ScanCheckpointRequest,
    SnapshotStatusRequest,
)

router = APIRouter(prefix="/api/v1")

ADMIN_RESOURCES = {
    "recruiters": Recruiter,
    "accounts": RecruitmentAccount,
    "jobs": RecruitmentJob,
    "job-aliases": JobAlias,
    "unmapped-jobs": UnmappedJob,
    "candidate-sources": CandidateSource,
    "interviews": Interview,
    "conflicts": Conflict,
    "notifications": NotificationOutbox,
    "candidate-sync": CandidateSyncOutbox,
    "lookup-alerts": DuplicateLookupAlert,
    "devices": PluginDevice,
    "worker-heartbeats": WorkerHeartbeat,
    "scan-checkpoints": ConversationScanCheckpoint,
    "binding-attempts": FeishuBindingAttempt,
    "audit-logs": AuditLog,
    "plugin-diagnostics": PluginDiagnostic,
}
SENSITIVE_COLUMNS = {"password_hash", "refresh_token_hash"}
EXPECTED_WORKERS = {
    "candidate-sync-worker": "飞书候选人同步",
    "notification-worker": "飞书通知",
    "data-retention-worker": "数据清理",
}


def serialize(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _status_counts(db: Session, company_id: str, model: Any) -> dict[str, int]:
    statement = select(model.status, func.count()).where(model.company_id == company_id).group_by(model.status)
    return {str(status): int(count) for status, count in db.execute(statement).all()}


def _worker_operations(db: Session, current: datetime) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    heartbeats = {row.worker_name: row for row in db.scalars(select(WorkerHeartbeat)).all()}
    workers: list[dict[str, Any]] = []
    alerts: list[dict[str, str]] = []
    for name, label in EXPECTED_WORKERS.items():
        row = heartbeats.get(name)
        last_seen = _aware_utc(row.last_seen_at) if row and row.last_seen_at else None
        stale = not last_seen or current - last_seen > timedelta(seconds=90)
        state = "MISSING" if not row else "STALE" if stale else row.status
        workers.append(
            {
                "name": name,
                "label": label,
                "status": state,
                "last_seen_at": row.last_seen_at if row else None,
                "last_success_at": row.last_success_at if row else None,
                "last_error_code": row.last_error_code if row else None,
                "total_processed": row.total_processed if row else 0,
            }
        )
        if state in {"MISSING", "STALE", "ERROR"}:
            detail = f"最近错误：{row.last_error_code}" if state == "ERROR" and row else "没有近期心跳"
            alerts.append(
                {
                    "id": f"worker-{name}",
                    "severity": "critical",
                    "title": f"{label} Worker 异常",
                    "detail": detail,
                }
            )
    return workers, alerts


def _queue_alerts(
    candidate_sync: dict[str, int],
    notifications: dict[str, int],
    snapshot_counts: dict[str, int],
    source_count: int,
    checkpoint_count: int,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    values = {
        "pending_sync": candidate_sync.get("PENDING", 0) + candidate_sync.get("PROCESSING", 0),
        "failed_sync": candidate_sync.get("FAILED", 0),
        "failed_notifications": notifications.get("FAILED", 0),
        "missing_snapshots": snapshot_counts.get("NONE", 0),
        "pending_snapshots": snapshot_counts.get("PENDING", 0),
        "failed_snapshots": snapshot_counts.get("FAILED", 0),
        "interrupted_snapshots": snapshot_counts.get("INTERRUPTED", 0),
    }
    alerts: list[dict[str, str]] = []
    definitions = (
        (values["failed_sync"], "candidate-sync-failed", "critical", "飞书候选人同步失败", "个任务已达到重试上限"),
        (values["pending_sync"], "candidate-sync-pending", "warning", "候选人同步队列有积压", "个任务等待或正在处理"),
        (values["failed_notifications"], "notification-failed", "critical", "飞书通知发送失败", "个通知已达到重试上限"),
    )
    for count, alert_id, severity, title, suffix in definitions:
        if count:
            alerts.append({"id": alert_id, "severity": severity, "title": title, "detail": f"{count} {suffix}"})
    if values["missing_snapshots"] or values["pending_snapshots"] or values["failed_snapshots"] or values["interrupted_snapshots"]:
        alerts.append(
            {
                "id": "snapshot-missing",
                "severity": "warning",
                "title": "聊天快照不完整",
                "detail": (
                    f"{values['missing_snapshots']} 条未采集，{values['pending_snapshots']} 条上传中，"
                    f"{values['failed_snapshots']} 条待重试，{values['interrupted_snapshots']} 条被用户操作中断"
                ),
            }
        )
    if source_count and not checkpoint_count:
        alerts.append(
            {
                "id": "checkpoint-missing",
                "severity": "warning",
                "title": "尚未建立隔夜补扫水位",
                "detail": "已有同步记录，但没有任何招聘账号的成功补扫水位",
            }
        )
    return alerts, values


def _operation_bindings(db: Session, company_id: str) -> list[dict[str, Any]]:
    source_count_rows = db.execute(
        select(RecruitmentAccount.recruiter_id, func.count(CandidateSource.id))
        .join(CandidateSource, CandidateSource.platform_account_id == RecruitmentAccount.id)
        .where(RecruitmentAccount.company_id == company_id)
        .group_by(RecruitmentAccount.recruiter_id)
    ).all()
    source_counts: dict[str, int] = {recruiter_id: int(count) for recruiter_id, count in source_count_rows}
    devices_by_recruiter: dict[str, list[PluginDevice]] = defaultdict(list)
    for device in db.scalars(select(PluginDevice).where(PluginDevice.company_id == company_id).order_by(PluginDevice.updated_at.desc())).all():
        devices_by_recruiter[device.recruiter_id].append(device)
    accounts_by_recruiter: dict[str, list[RecruitmentAccount]] = defaultdict(list)
    for account in db.scalars(
        select(RecruitmentAccount).where(
            RecruitmentAccount.company_id == company_id,
            RecruitmentAccount.status == "ACTIVE",
        )
    ).all():
        accounts_by_recruiter[account.recruiter_id].append(account)
    checkpoints_by_account: dict[str, list[ConversationScanCheckpoint]] = defaultdict(list)
    for checkpoint in db.scalars(select(ConversationScanCheckpoint).where(ConversationScanCheckpoint.company_id == company_id)).all():
        checkpoints_by_account[checkpoint.account_display_name].append(checkpoint)

    bindings: list[dict[str, Any]] = []
    recruiters = db.scalars(select(Recruiter).where(Recruiter.company_id == company_id).order_by(Recruiter.display_name)).all()
    for recruiter in recruiters:
        devices = devices_by_recruiter[recruiter.id]
        accounts = accounts_by_recruiter[recruiter.id]
        account_names = list(dict.fromkeys(account.account_display_name for account in accounts)) or [recruiter.display_name]
        checkpoints = [checkpoint for name in account_names for checkpoint in checkpoints_by_account[name]]
        bindings.append(
            {
                "recruiter_id": recruiter.id,
                "boss_accounts": account_names,
                "role": recruiter.role,
                "bound": bool(recruiter.feishu_open_id),
                "feishu_display_name": recruiter.feishu_display_name,
                "active_device_count": sum(device.status == "ACTIVE" for device in devices),
                "source_count": int(source_counts.get(recruiter.id, 0)),
                "last_checkpoint_at": max((item.completed_through_at for item in checkpoints), default=None),
                "devices": [
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "status": device.status,
                        "last_seen_at": device.last_seen_at,
                        "revoked_at": device.revoked_at,
                    }
                    for device in devices
                ],
            }
        )
    return bindings


def _recent_outbox_failures(db: Session, company_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    outbox_models: tuple[tuple[str, Any], ...] = (
        ("candidate_sync", CandidateSyncOutbox),
        ("notification", NotificationOutbox),
    )
    for kind, model in outbox_models:
        rows = db.scalars(
            select(model).where(model.company_id == company_id, model.status.in_({"FAILED", "PENDING"})).order_by(model.updated_at.desc()).limit(20)
        ).all()
        output.extend(
            {
                "kind": kind,
                "id": row.id,
                "status": row.status,
                "retry_count": row.retry_count,
                "last_error": row.last_error,
                "updated_at": row.updated_at,
            }
            for row in rows
        )
    return sorted(output, key=lambda item: str(item["updated_at"]), reverse=True)[:20]


def _recent_diagnostics(db: Session, company_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(PluginDiagnostic).where(PluginDiagnostic.company_id == company_id).order_by(PluginDiagnostic.created_at.desc()).limit(20)).all()
    fields = (
        "id",
        "platform",
        "adapter_version",
        "page_type",
        "account_status",
        "candidate_status",
        "job_status",
        "platform_id_status",
        "created_at",
    )
    return [
        {
            **{field: getattr(row, field) for field in fields},
            "error_codes": row.error_codes_json,
            "context": row.sanitized_context_json,
        }
        for row in rows
    ]


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _plugin_owned_sources(actor: Actor, source_id: str, related_source_ids: str, db: Session) -> tuple[CandidateSource, list[str]]:
    source = db.get(CandidateSource, source_id)
    if not source or source.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人记录不存在", 404)
    if actor.id:
        account = db.get(RecruitmentAccount, source.platform_account_id) if source.platform_account_id else None
        owner = db.get(Recruiter, account.recruiter_id) if account else None
        current = db.get(Recruiter, actor.id)
        same_bound_feishu = bool(owner and current and owner.feishu_open_id and owner.feishu_open_id == current.feishu_open_id)
        same_bound_name = bool(account and current and account.account_display_name == (current.feishu_display_name or current.display_name))
        if not account or (account.recruiter_id != actor.id and not same_bound_feishu and not same_bound_name):
            raise ApplicationError("CANDIDATE_SOURCE_FORBIDDEN", "只能上传当前招聘账号自己的候选人资料", 403)
    source_ids = list(dict.fromkeys([source_id, *[value.strip() for value in related_source_ids.split(",") if value.strip()]]))
    related = [db.get(CandidateSource, value) for value in source_ids]
    if any(
        not item
        or item.company_id != actor.company_id
        or item.platform_account_id != source.platform_account_id
        or item.candidate_identity_signature != source.candidate_identity_signature
        for item in related
    ):
        raise ApplicationError("RELATED_SOURCE_MISMATCH", "附件只能关联同一招聘账号下的同一候选人", 400)
    return source, source_ids


def _valid_snapshot_image(content: bytes, content_type: str) -> bool:
    return (content_type == "image/jpeg" and content.startswith(b"\xff\xd8")) or (content_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))


def plugin_company_for_account(actor: Actor, account_display_name: str | None, db: Session) -> str:
    if not actor.id or get_settings().is_development:
        return actor.company_id
    display_name = (account_display_name or "").strip()
    mapped = db.scalar(
        select(RecruitmentAccount.id).where(
            RecruitmentAccount.company_id == actor.company_id,
            RecruitmentAccount.recruiter_id == actor.id,
            RecruitmentAccount.account_display_name == display_name,
            RecruitmentAccount.status == "ACTIVE",
        )
    )
    recruiter = db.get(Recruiter, actor.id)
    bound_feishu_name = recruiter.feishu_display_name if recruiter else None
    if actor.display_name != display_name and display_name != bound_feishu_name and not mapped:
        raise ApplicationError("BOSS_ACCOUNT_FORBIDDEN", "当前飞书身份未绑定这个 BOSS 招聘账号", 403)
    return actor.company_id


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
    return _password_login(body, db)


@router.post("/auth/login")
def admin_login(body: DevLoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not get_settings().is_development:
        raise ApplicationError("PASSWORD_LOGIN_DISABLED", "管理后台请使用飞书登录", 404)
    user = _authenticated_user(body, db)
    if user.role not in ADMIN_ROLES:
        raise ApplicationError("ADMIN_LOGIN_REQUIRED", "该账号没有开发管理后台权限", 403)
    return _login_tokens(user)


def _authenticated_user(body: DevLoginRequest, db: Session) -> Recruiter:
    user = db.scalar(select(Recruiter).where(Recruiter.email == body.email, Recruiter.status == "ACTIVE"))
    if not user or not verify_password(user.password_hash, body.password):
        raise ApplicationError("INVALID_CREDENTIALS", "邮箱或密码错误", 401)
    return user


def _password_login(body: DevLoginRequest, db: Session) -> dict[str, Any]:
    return _login_tokens(_authenticated_user(body, db))


def _login_tokens(user: Recruiter) -> dict[str, Any]:
    return {
        "access_token": make_token(user.id, user.company_id, user.role),
        "refresh_token": make_token(user.id, user.company_id, user.role, "refresh"),
        "token_type": "bearer",
        "user": {"id": user.id, "display_name": user.display_name, "role": user.role},
    }


@router.post("/auth/refresh")
def refresh(body: dict[str, str], db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        payload = decode_token(body.get("refresh_token", ""), "refresh")
    except Exception as exc:
        raise ApplicationError("INVALID_REFRESH_TOKEN", "刷新令牌无效", 401) from exc
    user = db.get(Recruiter, payload["sub"])
    if not user or user.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "账号不可用", 401)
    device_id = str(payload.get("device_id") or body.get("device_id") or "")
    if device_id:
        device = db.scalar(
            select(PluginDevice).where(PluginDevice.device_id == device_id, PluginDevice.recruiter_id == user.id, PluginDevice.status == "ACTIVE")
        )
        if not device or device.refresh_token_hash != hash_token(body.get("refresh_token", "")):
            raise ApplicationError("DEVICE_REVOKED", "扩展设备登录已失效，请重新使用飞书登录", 401)
        device.last_seen_at = now()
        db.commit()
    return {"access_token": make_token(user.id, user.company_id, user.role, device_id=device_id or None), "token_type": "bearer"}


@router.post("/auth/logout")
def logout(response: Response, _: Actor = Depends(current_actor)) -> dict[str, bool]:
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return {"success": True}


@router.get("/auth/me")
def me(actor: Actor = Depends(current_actor)) -> dict[str, str]:
    return actor.__dict__


@router.get("/plugin/me")
def plugin_me(actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, str]:
    """Return the bound Feishu display name used as the sync recruiter."""
    recruiter = db.get(Recruiter, actor.id)
    return {"display_name": recruiter.feishu_display_name if recruiter and recruiter.feishu_display_name else actor.display_name}


@router.post("/auth/feishu/web/start")
def feishu_web_login_start(company_id: str = Depends(bootstrap_company_id), db: Session = Depends(get_db)) -> dict[str, str]:
    settings = get_settings()
    if settings.feishu_mode != "real":
        raise ApplicationError("FEISHU_LOGIN_UNAVAILABLE", "当前未启用真实飞书模式", 503)
    state = secrets.token_urlsafe(32)
    attempt = FeishuBindingAttempt(
        company_id=company_id,
        account_display_name="__ADMIN_WEB__",
        action="web_login",
        state_hash=hash_token(state),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(attempt)
    db.commit()
    return {"authorization_url": FeishuOAuthClient(settings).authorization_url(state)}


@router.post("/auth/devices/{device_id}/revoke")
def revoke_device(device_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    device = db.get(PluginDevice, device_id)
    if not device or (device.recruiter_id != actor.id and actor.role not in ADMIN_ROLES):
        raise ApplicationError("DEVICE_NOT_FOUND", "设备不存在", 404)
    device.status, device.revoked_at = "REVOKED", now()
    db.commit()
    return {"success": True}


@router.get("/plugin/feishu-binding/status")
def feishu_binding_status(
    account_display_name: str = Query(min_length=1, max_length=100), actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, account_display_name, db)
    recruiter = RecruitmentCollaborationService(db).page_recruiter(company_id, account_display_name.strip())
    return {
        "account_display_name": account_display_name.strip(),
        "bound": bool(recruiter and recruiter.feishu_open_id),
        "feishu_display_name": recruiter.feishu_display_name if recruiter and recruiter.feishu_open_id else None,
    }


@router.post("/plugin/feishu-binding/start")
def feishu_binding_start(body: FeishuBindingStartRequest, company_id: str = Depends(bootstrap_company_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if settings.feishu_mode != "real":
        raise ApplicationError("FEISHU_BINDING_UNAVAILABLE", "当前未启用真实飞书模式", 503)
    display_name = body.account_display_name.strip()
    recruiter = RecruitmentCollaborationService(db).page_recruiter(company_id, display_name)
    if body.action == "unbind" and (not recruiter or not recruiter.feishu_open_id):
        raise ApplicationError("FEISHU_NOT_BOUND", "当前 BOSS 招聘人员尚未绑定飞书", 409)
    state = secrets.token_urlsafe(32)
    poll_token = secrets.token_urlsafe(32) if body.device_id and body.action == "bind" else None
    attempt = FeishuBindingAttempt(
        company_id=company_id,
        account_display_name=display_name,
        action=body.action,
        state_hash=hash_token(state),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        device_id=body.device_id,
        device_name=body.device_name,
        device_poll_token_hash=hash_token(poll_token) if poll_token else None,
    )
    db.add(attempt)
    db.commit()
    return {
        "authorization_url": FeishuOAuthClient(settings).authorization_url(state),
        "expires_in": 600,
        "action": body.action,
        "attempt_id": attempt.id,
        "poll_token": poll_token,
    }


def _disconnect_recruiter_feishu(db: Session, recruiter: Recruiter) -> None:
    """Remove a Feishu identity and invalidate every extension session it owned."""
    disconnected_at = now()
    recruiter.feishu_open_id = None
    recruiter.feishu_user_id = None
    recruiter.feishu_display_name = None
    for row in db.scalars(
        select(NotificationOutbox).where(NotificationOutbox.recipient_recruiter_id == recruiter.id, NotificationOutbox.status == "PENDING")
    ).all():
        row.status = "CANCELLED"
    for device in db.scalars(select(PluginDevice).where(PluginDevice.recruiter_id == recruiter.id, PluginDevice.status == "ACTIVE")).all():
        device.status = "REVOKED"
        device.revoked_at = disconnected_at
        device.refresh_token_hash = None


@router.get("/auth/feishu/callback", response_class=HTMLResponse)
async def feishu_callback(code: str = Query(...), state: str = Query(...), db: Session = Depends(get_db)) -> Response:
    attempt = _pending_binding_attempt(db, state)
    identity = await FeishuOAuthClient(get_settings()).exchange_code(code)
    return _complete_web_login(db, attempt, identity) if attempt.action == "web_login" else _complete_boss_binding(db, attempt, identity)


def _pending_binding_attempt(db: Session, state: str) -> FeishuBindingAttempt:
    attempt = db.scalar(select(FeishuBindingAttempt).where(FeishuBindingAttempt.state_hash == hash_token(state)))
    if not attempt or attempt.status != "PENDING":
        raise ApplicationError("FEISHU_BINDING_STATE_INVALID", "飞书绑定请求无效或已使用", 400)
    if _aware_utc(attempt.expires_at) < datetime.now(timezone.utc):
        attempt.status = "EXPIRED"
        db.commit()
        raise ApplicationError("FEISHU_BINDING_STATE_EXPIRED", "飞书绑定请求已过期，请回到扩展重新发起", 400)
    return attempt


def _complete_web_login(db: Session, attempt: FeishuBindingAttempt, identity: FeishuIdentity) -> Response:
    recruiter = db.scalar(
        select(Recruiter).where(Recruiter.company_id == attempt.company_id, Recruiter.feishu_open_id == identity.open_id, Recruiter.status == "ACTIVE")
    )
    if not recruiter or recruiter.role not in ADMIN_ROLES:
        raise ApplicationError("ADMIN_LOGIN_REQUIRED", "该飞书账号没有开发管理后台权限", 403)
    attempt.recruiter_id, attempt.status, attempt.consumed_at = recruiter.id, "CONSUMED", now()
    db.commit()
    settings = get_settings()
    response = RedirectResponse(f"{settings.public_web_url.rstrip('/')}/recruitment/overview", status_code=303)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        make_token(recruiter.id, recruiter.company_id, recruiter.role, "web_session"),
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.public_web_url.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


def _complete_boss_binding(db: Session, attempt: FeishuBindingAttempt, identity: FeishuIdentity) -> HTMLResponse:
    service = RecruitmentCollaborationService(db)
    # The extension's simplified flow no longer reads a BOSS account name;
    # use the authenticated Feishu display name as the recruiter identity.
    account_name = identity.display_name if attempt.account_display_name == "飞书同步账号" else attempt.account_display_name
    recruiter = None
    if attempt.action == "unbind" and attempt.account_display_name == "飞书同步账号":
        recruiter = db.scalar(select(Recruiter).where(Recruiter.company_id == attempt.company_id, Recruiter.feishu_open_id == identity.open_id, Recruiter.status == "ACTIVE"))
    if recruiter is None:
        recruiter = service.page_recruiter(attempt.company_id, account_name, create=attempt.action == "bind")
    if not recruiter:
        raise ApplicationError("PAGE_RECRUITER_NOT_FOUND", "对应的 BOSS 招聘人员不存在", 404)
    if attempt.action == "bind":
        if recruiter.feishu_open_id and recruiter.feishu_open_id != identity.open_id:
            raise ApplicationError("BOSS_RECRUITER_ALREADY_BOUND", "该 BOSS 招聘人员已绑定其他飞书账号，请先由原账号解绑", 409)
        other = db.scalar(
            select(Recruiter).where(Recruiter.company_id == attempt.company_id, Recruiter.feishu_open_id == identity.open_id, Recruiter.id != recruiter.id)
        )
        previous_account = other.display_name if other else None
        if other:
            # A Feishu identity is also the production admin login identity.
            # Moving that identity to another BOSS recruiter row must not
            # silently remove the person's access to the operations console.
            if other.role in ADMIN_ROLES and recruiter.role not in ADMIN_ROLES:
                recruiter.role = other.role
            _disconnect_recruiter_feishu(db, other)
        recruiter.feishu_open_id = identity.open_id
        recruiter.feishu_user_id = identity.user_id
        recruiter.feishu_display_name = identity.display_name
        attempt.recruiter_id = recruiter.id
        detail = f"{account_name} 已绑定到 {identity.display_name}"
        if previous_account:
            detail = f"已自动退出上一个 BOSS 招聘账号 {previous_account}；{detail}"
        title = "飞书绑定成功"
    else:
        if recruiter.feishu_open_id != identity.open_id:
            raise ApplicationError("FEISHU_UNBIND_FORBIDDEN", "只能由当前已绑定的飞书账号执行解绑", 403)
        _disconnect_recruiter_feishu(db, recruiter)
        title, detail = "飞书解绑成功", f"{account_name} 已停止接收飞书私聊提醒"
    db.add(
        AuditLog(
            company_id=attempt.company_id,
            actor_id=recruiter.id,
            action="FEISHU_BOUND" if attempt.action == "bind" else "FEISHU_UNBOUND",
            entity_type="recruiter",
            entity_id=recruiter.id,
            after_json={"boss_account_name": attempt.account_display_name, "feishu_display_name": identity.display_name, "device_id": attempt.device_id},
        )
    )
    attempt.status = "APPROVED" if attempt.action == "bind" and attempt.device_poll_token_hash else "CONSUMED"
    attempt.consumed_at = now() if attempt.status == "CONSUMED" else None
    db.commit()
    safe_title, safe_detail = escape(title), escape(detail)
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{safe_title}</title><style>body{{font:16px system-ui;max-width:520px;margin:80px auto;padding:24px}}h2{{color:#0f766e}}</style><h2>{safe_title}</h2><p>{safe_detail}</p><p>现在可以关闭此页面，重新打开招聘协同助手查看状态。</p>"
    )


@router.post("/auth/feishu/device/poll")
def feishu_device_poll(body: FeishuDevicePollRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    attempt = db.get(FeishuBindingAttempt, body.attempt_id)
    if not attempt or not attempt.device_poll_token_hash or not secrets.compare_digest(attempt.device_poll_token_hash, hash_token(body.poll_token)):
        raise ApplicationError("DEVICE_LOGIN_INVALID", "扩展飞书登录请求无效", 400)
    expires_at = attempt.expires_at if attempt.expires_at.tzinfo else attempt.expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise ApplicationError("DEVICE_LOGIN_EXPIRED", "扩展飞书登录已过期，请重新发起", 400)
    if attempt.status == "PENDING":
        return {"status": "PENDING"}
    if attempt.status != "APPROVED" or not attempt.recruiter_id or not attempt.device_id:
        raise ApplicationError("DEVICE_LOGIN_CONSUMED", "扩展飞书登录请求已使用", 409)
    recruiter = db.get(Recruiter, attempt.recruiter_id)
    if not recruiter or recruiter.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "招聘账号不可用", 401)
    existing = db.scalar(select(PluginDevice).where(PluginDevice.device_id == attempt.device_id))
    if existing and existing.recruiter_id != recruiter.id:
        if existing.status != "REVOKED":
            raise ApplicationError("DEVICE_ALREADY_BOUND", "该浏览器已绑定其他招聘者", 409)
        existing.company_id = recruiter.company_id
        existing.recruiter_id = recruiter.id
    refresh_token = make_token(recruiter.id, recruiter.company_id, recruiter.role, "refresh", attempt.device_id)
    if existing:
        device = existing
        device.status, device.revoked_at = "ACTIVE", None
        device.device_name = attempt.device_name or device.device_name
        device.refresh_token_hash = hash_token(refresh_token)
        device.last_seen_at = now()
    else:
        device = PluginDevice(
            company_id=recruiter.company_id,
            recruiter_id=recruiter.id,
            device_id=attempt.device_id,
            device_name=attempt.device_name or "Chrome",
            refresh_token_hash=hash_token(refresh_token),
        )
        db.add(device)
    attempt.status, attempt.consumed_at = "CONSUMED", now()
    db.commit()
    return {
        "status": "APPROVED",
        "access_token": make_token(recruiter.id, recruiter.company_id, recruiter.role, device_id=attempt.device_id),
        "refresh_token": refresh_token,
        "device_id": attempt.device_id,
        "user": {"display_name": recruiter.feishu_display_name or recruiter.display_name, "boss_account_name": recruiter.display_name},
    }


def _ignored_boss_page(body: ContextResolveRequest) -> bool:
    return body.platform == "boss" and urlparse(str(body.page_url)).path.rstrip("/") != "/web/chat/index"


def _validated_plugin_payload(body: ContextResolveRequest) -> dict[str, Any]:
    page_url = urlparse(str(body.page_url))
    if body.platform == "boss" and page_url.hostname not in {"zhipin.com", "www.zhipin.com"}:
        raise ApplicationError("UNTRUSTED_BOSS_ORIGIN", "BOSS 数据只能来自已允许的 zhipin.com 沟通页", 400)
    payload = {**body.model_dump(), "page_url": str(body.page_url)}
    if body.platform == "boss":
        # The current on-page adapter has no verified company/global ID scope.
        payload["platform_id_scope"] = "UNKNOWN"
    return payload


@router.post("/plugin/context/check")
def check_context(body: ContextResolveRequest, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    if _ignored_boss_page(body):
        return {
            "candidate_source_id": None,
            "account_mapping": {"status": "NOT_REQUIRED"},
            "job_mapping": {"status": "NOT_EVALUATED"},
            "result_type": "IGNORED_PAGE",
            "ui": {"severity": "success", "title": "", "message": ""},
            "matches": [],
            "available_actions": [],
        }
    # Preserve Pydantic-validated datetime objects for SQLAlchemy. Only the URL
    # wrapper needs conversion before reaching the application service.
    return RecruitmentCollaborationService(db).check_context(company_id, _validated_plugin_payload(body))


@router.post("/plugin/engagements/message-sent")
def message_sent(body: MessageSentRequest, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    if _ignored_boss_page(body):
        raise ApplicationError("UNSUPPORTED_PAGE", "仅候选人沟通页可登记招聘消息")
    if _aware_utc(body.sent_at) > datetime.now(timezone.utc) + timedelta(hours=24):
        raise ApplicationError("EVENT_TIME_INVALID", "发送时间不能晚于服务器时间", 400)
    payload = _validated_plugin_payload(body)
    return RecruitmentCollaborationService(db).record_message_sent(company_id, payload)


@router.post("/plugin/conversations/sync")
def sync_conversation(body: ConversationSyncRequest, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    if _ignored_boss_page(body):
        raise ApplicationError("UNSUPPORTED_PAGE", "仅候选人沟通页可同步会话")
    if not body.has_recruiter_outbound:
        return {
            "candidate_source_id": None,
            "result_type": "INBOUND_ONLY",
            "matches": [],
            "ui": {"severity": "success", "title": "", "message": ""},
            "available_actions": [],
            "account_mapping": {"status": "NOT_REQUIRED"},
            "job_mapping": {"status": "NOT_EVALUATED"},
        }
    if _aware_utc(body.sent_at) > datetime.now(timezone.utc) + timedelta(hours=24):
        raise ApplicationError("EVENT_TIME_INVALID", "会话时间不能晚于服务器时间", 400)
    return RecruitmentCollaborationService(db).record_message_sent(company_id, _validated_plugin_payload(body))


@router.get("/plugin/conversations/checkpoint")
def get_scan_checkpoint(
    account_display_name: str = Query(min_length=1, max_length=100), platform: str = "boss", actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, account_display_name, db)
    row = db.scalar(
        select(ConversationScanCheckpoint).where(
            ConversationScanCheckpoint.company_id == company_id,
            ConversationScanCheckpoint.platform == platform,
            ConversationScanCheckpoint.account_display_name == account_display_name.strip(),
        )
    )
    if not row:
        return {"completed_through_at": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(), "cursor": {}, "initial": True}
    return {"completed_through_at": row.completed_through_at.isoformat(), "cursor": row.cursor_json, "initial": False}


@router.put("/plugin/conversations/checkpoint")
def put_scan_checkpoint(body: ScanCheckpointRequest, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    if _aware_utc(body.completed_through_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ApplicationError("CHECKPOINT_TIME_INVALID", "补扫水位不能晚于服务器时间", 400)
    row = db.scalar(
        select(ConversationScanCheckpoint).where(
            ConversationScanCheckpoint.company_id == company_id,
            ConversationScanCheckpoint.platform == body.platform,
            ConversationScanCheckpoint.account_display_name == body.account_display_name.strip(),
        )
    )
    if body.cursor.get("complete") is not True:
        # Older extensions reported the newest individual candidate as if an
        # entire newest-to-oldest traversal had completed. Never let a partial
        # report move the high-water mark past candidates that were not seen.
        completed = row.completed_through_at if row else datetime.now(timezone.utc) - timedelta(hours=48)
        return {"completed_through_at": completed.isoformat(), "cursor": row.cursor_json if row else {}, "accepted": False}
    if row:
        stored = row.completed_through_at if row.completed_through_at.tzinfo else row.completed_through_at.replace(tzinfo=timezone.utc)
        incoming = body.completed_through_at if body.completed_through_at.tzinfo else body.completed_through_at.replace(tzinfo=timezone.utc)
        if incoming > stored:
            row.completed_through_at = body.completed_through_at
        row.cursor_json = body.cursor
    else:
        row = ConversationScanCheckpoint(
            company_id=company_id,
            platform=body.platform,
            account_display_name=body.account_display_name.strip(),
            completed_through_at=body.completed_through_at,
            cursor_json=body.cursor,
        )
        db.add(row)
    db.commit()
    return {"completed_through_at": row.completed_through_at.isoformat(), "cursor": row.cursor_json, "accepted": True}


@router.post("/plugin/conversations/{source_id}/snapshot")
async def upload_conversation_snapshot(
    source_id: str,
    files: list[UploadFile] = File(...),
    snapshot_hash: str = Form(...),
    related_source_ids: str = Form(default=""),
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source, source_ids = _plugin_owned_sources(actor, source_id, related_source_ids, db)
    if not files or len(files) > 20:
        raise ApplicationError("SNAPSHOT_PARTS_INVALID", "快照分片数量无效")
    contents: list[bytes] = []
    calculated = hashlib.sha256()
    for upload in files:
        content = await upload.read(15 * 1024 * 1024 + 1)
        content_type = (upload.content_type or "").lower()
        if not content or len(content) > 15 * 1024 * 1024 or not _valid_snapshot_image(content, content_type):
            raise ApplicationError("SNAPSHOT_PART_INVALID", "快照分片必须是小于 15MB 的 JPEG 或 PNG 图片")
        contents.append(content)
        calculated.update(content)
    if calculated.hexdigest() != snapshot_hash:
        raise ApplicationError("SNAPSHOT_HASH_MISMATCH", "快照校验失败")
    source.snapshot_status = "PENDING"
    db.commit()
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    client = BitableSyncClient(get_settings())
    try:
        tokens = [await run_in_threadpool(client.upload_snapshot, f"boss-chat-{source_id}-{index + 1}.jpg", content) for index, content in enumerate(contents)]
        service = RecruitmentCollaborationService(db)
        for value in source_ids:
            service.attach_snapshot(actor.company_id, value, tokens, snapshot_hash)
        return {"candidate_source_ids": source_ids, "snapshot_status": "READY", "part_count": len(tokens)}
    except Exception:
        service = RecruitmentCollaborationService(db)
        for value in source_ids:
            failed_source = db.get(CandidateSource, value)
            if failed_source and failed_source.snapshot_status != "READY":
                failed_source.snapshot_status = "FAILED"
                service._queue_source_sync(failed_source)
        db.commit()
        raise


@router.put("/plugin/conversations/snapshot-status")
def update_conversation_snapshot_status(
    body: SnapshotStatusRequest,
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    updated: list[str] = []
    service = RecruitmentCollaborationService(db)
    for source_id in dict.fromkeys(body.candidate_source_ids):
        source, _ = _plugin_owned_sources(actor, source_id, "", db)
        # Never downgrade a successfully uploaded snapshot because of a later
        # capture attempt. Error detail stays in sanitized diagnostics only.
        if source.snapshot_status == "READY":
            continue
        source.snapshot_status = body.status
        service._queue_source_sync(source)
        updated.append(source.id)
    db.commit()
    return {"candidate_source_ids": updated, "snapshot_status": body.status}


@router.post("/plugin/conversations/{source_id}/resume")
async def upload_candidate_resume(
    source_id: str,
    file: UploadFile = File(...),
    resume_hash: str = Form(...),
    related_source_ids: str = Form(default=""),
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source, source_ids = _plugin_owned_sources(actor, source_id, related_source_ids, db)
    allowed_types = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
    }
    content_type = (file.content_type or "").lower()
    if content_type not in allowed_types:
        raise ApplicationError("RESUME_TYPE_INVALID", "简历只支持 PDF、Word、JPG 或 PNG", 400)
    content = await file.read(25 * 1024 * 1024 + 1)
    if not content or len(content) > 25 * 1024 * 1024:
        raise ApplicationError("RESUME_SIZE_INVALID", "简历文件不能超过 25MB", 400)
    if hashlib.sha256(content).hexdigest() != resume_hash:
        raise ApplicationError("RESUME_HASH_MISMATCH", "简历校验失败", 400)
    safe_name = Path(file.filename or "candidate-resume.pdf").name[:255]
    source.resume_status = "PENDING"
    db.commit()
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    try:
        token = await run_in_threadpool(BitableSyncClient(get_settings()).upload_resume, safe_name, content, content_type)
        service = RecruitmentCollaborationService(db)
        for value in source_ids:
            service.attach_resume(actor.company_id, value, token, resume_hash, safe_name)
        return {"candidate_source_ids": source_ids, "resume_status": "READY", "file_name": safe_name}
    except Exception:
        service = RecruitmentCollaborationService(db)
        for value in source_ids:
            failed_source = db.get(CandidateSource, value)
            if failed_source and failed_source.resume_status != "READY":
                failed_source.resume_status = "FAILED"
                service._queue_source_sync(failed_source)
        db.commit()
        raise


@router.post("/plugin/events")
def event(body: EventRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return RecruitmentCollaborationService(db).record_event(actor.id, actor.company_id, body.model_dump())


@router.post("/plugin/interviews")
def interview(body: InterviewRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(CandidateSource, body.candidate_source_id)
    if not source or source.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    row = Interview(
        company_id=actor.company_id,
        candidate_source_id=source.id,
        job_id=source.job_id,
        recruiter_id=actor.id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
        location_type=body.location_type,
        location_text=body.location_text,
        notes=body.notes,
    )
    db.add(row)
    db.flush()
    RecruitmentCollaborationService(db).record_event(
        actor.id,
        actor.company_id,
        {"candidate_source_id": source.id, "event_type": "INTERVIEW_INVITED", "idempotency_key": body.idempotency_key, "reason": None},
    )
    return serialize(row)


@router.get("/plugin/candidates/{source_id}/timeline")
def timeline(source_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    source = db.get(CandidateSource, source_id)
    if not source or source.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    return [
        serialize(row)
        for row in db.scalars(
            select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id).order_by(RecruitmentEvent.event_time.desc())
        ).all()
    ]


@router.post("/plugin/diagnostics")
def diagnostics(body: DiagnosticRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, str]:
    forbidden = {"html", "cookie", "token", "password"}
    clean = {key: value for key, value in body.sanitized_context.items() if key.lower() not in forbidden}
    row = PluginDiagnostic(
        company_id=actor.company_id,
        recruiter_id=actor.id,
        platform=body.platform,
        adapter_version=body.adapter_version,
        page_type=body.page_type,
        account_status=body.account_status,
        candidate_status=body.candidate_status,
        job_status=body.job_status,
        platform_id_status=body.platform_id_status,
        error_codes_json=body.error_codes,
        sanitized_context_json=clean,
    )
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.get("/conflicts")
def conflicts(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [serialize(row) for row in db.scalars(select(Conflict).where(Conflict.company_id == actor.company_id).order_by(Conflict.created_at.desc())).all()]


@router.get("/conflicts/{conflict_id}")
def conflict_detail(conflict_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id:
        raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    return serialize(row)


@router.post("/conflicts/{conflict_id}/exclude")
def exclude(conflict_id: str, body: ReasonRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    RecruitmentCollaborationService(db).exclude_conflict(conflict_id, actor.id, actor.company_id, body.reason)
    return {"success": True}


@router.post("/conflicts/{conflict_id}/{action}")
def conflict_action(
    conflict_id: str, action: str, body: ReasonRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)
) -> dict[str, bool]:
    mapping = {"acknowledge": "ACKNOWLEDGED", "request-transfer": "TRANSFER_REQUESTED", "transfer": "TRANSFERRED", "close": "CLOSED", "continue": "OPEN"}
    if action not in mapping:
        raise ApplicationError("ACTION_NOT_SUPPORTED", "不支持的操作", 404)
    if action == "continue" and not body.reason.strip():
        raise ApplicationError("REASON_REQUIRED", "继续沟通必须填写原因")
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id:
        raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    if action == "transfer" and actor.role not in ADMIN_ROLES:
        raise ApplicationError("FORBIDDEN", "仅管理员可执行转交", 403)
    row.status = mapping[action]
    row.resolution = body.reason
    db.commit()
    return {"success": True}


@router.get("/admin/overview")
def overview(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    def count(model: Any, *conditions: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    return {
        "candidate_queries": count(CandidateSource, CandidateSource.company_id == actor.company_id),
        "engagements": count(Engagement, Engagement.company_id == actor.company_id),
        "open_conflicts": count(Conflict, Conflict.company_id == actor.company_id, Conflict.status == "OPEN"),
        "interviews": count(Interview, Interview.company_id == actor.company_id, Interview.status == "SCHEDULED"),
        "unmapped_jobs": count(UnmappedJob, UnmappedJob.company_id == actor.company_id, UnmappedJob.resolved_job_id.is_(None)),
        "notification_failures": count(NotificationOutbox, NotificationOutbox.company_id == actor.company_id, NotificationOutbox.status == "FAILED"),
    }


@router.get("/admin/operations")
def operations(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    current = now()
    candidate_sync = _status_counts(db, actor.company_id, CandidateSyncOutbox)
    notifications = _status_counts(db, actor.company_id, NotificationOutbox)
    snapshot_statement = (
        select(CandidateSource.snapshot_status, func.count()).where(CandidateSource.company_id == actor.company_id).group_by(CandidateSource.snapshot_status)
    )
    snapshot_counts = {str(status): int(count) for status, count in db.execute(snapshot_statement).all()}
    source_count = int(db.scalar(select(func.count()).select_from(CandidateSource).where(CandidateSource.company_id == actor.company_id)) or 0)
    checkpoint_count = int(
        db.scalar(select(func.count()).select_from(ConversationScanCheckpoint).where(ConversationScanCheckpoint.company_id == actor.company_id)) or 0
    )
    lookup_alert_count = int(db.scalar(select(func.count()).select_from(DuplicateLookupAlert).where(DuplicateLookupAlert.company_id == actor.company_id)) or 0)
    workers, alerts = _worker_operations(db, current)
    queue_alerts, queue_values = _queue_alerts(candidate_sync, notifications, snapshot_counts, source_count, checkpoint_count)
    alerts.extend(queue_alerts)
    bindings = _operation_bindings(db, actor.company_id)
    settings = get_settings()
    return {
        "generated_at": current,
        "overall_status": "critical" if any(item["severity"] == "critical" for item in alerts) else "warning" if alerts else "healthy",
        "alerts": alerts,
        "metrics": {
            "candidate_sources": source_count,
            "candidate_sync_pending": queue_values["pending_sync"],
            "candidate_sync_failed": queue_values["failed_sync"],
            "candidate_sync_sent": candidate_sync.get("SENT", 0),
            "snapshot_ready": snapshot_counts.get("READY", 0),
            "snapshot_missing": queue_values["missing_snapshots"],
            "active_devices": sum(item["active_device_count"] for item in bindings),
            "bound_recruiters": sum(item["bound"] for item in bindings),
            "checkpoints": checkpoint_count,
            "lookup_alerts": lookup_alert_count,
        },
        "workers": workers,
        "queues": {"candidate_sync": candidate_sync, "notifications": notifications, "snapshots": snapshot_counts},
        "bindings": bindings,
        "recent_failures": _recent_outbox_failures(db, actor.company_id),
        "diagnostics": _recent_diagnostics(db, actor.company_id),
        "configuration": {
            "environment": settings.app_env,
            "feishu_mode": settings.feishu_mode,
            "feishu_app_configured": bool(settings.feishu_app_id and settings.feishu_app_secret),
            "feishu_candidate_table_configured": bool(settings.feishu_bitable_app_token and settings.feishu_bitable_candidate_table_id),
            "retention_days": {
                "candidate": settings.candidate_cache_days,
                "diagnostic": settings.diagnostic_retention_days,
                "event": settings.event_retention_days,
                "audit": settings.audit_retention_days,
            },
        },
    }


@router.post("/admin/devices/{device_id}/revoke")
def admin_revoke_device(device_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    device = db.scalar(select(PluginDevice).where(PluginDevice.device_id == device_id, PluginDevice.company_id == actor.company_id))
    if not device:
        raise ApplicationError("DEVICE_NOT_FOUND", "扩展设备不存在", 404)
    device.status, device.revoked_at, device.refresh_token_hash = "REVOKED", now(), None
    db.commit()
    return {"success": True}


@router.post("/admin/candidate-sync/{outbox_id}/retry")
def admin_retry_candidate_sync(outbox_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.id == outbox_id, CandidateSyncOutbox.company_id == actor.company_id))
    if not row or row.status not in {"FAILED", "PENDING"}:
        raise ApplicationError("SYNC_RETRY_NOT_ALLOWED", "同步任务不存在或当前状态不允许重试", 409)
    if not row.payload_json:
        raise ApplicationError("SYNC_PAYLOAD_EXPIRED", "同步载荷已按保留期限清除，请由扩展重新同步该候选人", 409)
    row.status, row.retry_count, row.next_retry_at, row.last_error = "PENDING", 0, now(), None
    db.commit()
    return {"success": True}


@router.get("/admin/{resource}")
def admin_list(resource: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    model = ADMIN_RESOURCES.get(resource)
    if not model:
        raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在", 404)
    statement = select(model)
    if hasattr(model, "company_id"):
        statement = statement.where(model.company_id == actor.company_id)
    rows = db.scalars(statement.limit(500)).all()
    output = [serialize(row) for row in rows]
    for item in output:
        for column in SENSITIVE_COLUMNS:
            item.pop(column, None)
    return output


@router.get("/admin/mock-feishu/messages")
def mock_feishu_messages(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    recruiter_ids = select(Recruiter.id).where(Recruiter.company_id == actor.company_id)
    rows = db.scalars(
        select(MockFeishuMessage).where(MockFeishuMessage.recipient_recruiter_id.in_(recruiter_ids)).order_by(MockFeishuMessage.created_at.desc())
    ).all()
    return [serialize(row) for row in rows]


@router.get("/admin/candidate-sources/{source_id}")
def candidate_source_detail(source_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(CandidateSource, source_id)
    if not row or row.company_id != actor.company_id:
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    data = serialize(row)
    data["events"] = [
        serialize(item)
        for item in db.scalars(
            select(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id == source_id).order_by(RecruitmentEvent.event_time.desc())
        ).all()
    ]
    data["interviews"] = [
        serialize(item)
        for item in db.scalars(select(Interview).where(Interview.candidate_source_id == source_id).order_by(Interview.scheduled_at.desc())).all()
    ]
    return data


@router.patch("/admin/unmapped-jobs/{unmapped_id}")
def map_unmapped_job(unmapped_id: str, body: dict[str, str], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(UnmappedJob, unmapped_id)
    job = db.get(RecruitmentJob, body.get("job_id", ""))
    if not row or row.company_id != actor.company_id or not job or job.company_id != actor.company_id:
        raise ApplicationError("MAPPING_TARGET_NOT_FOUND", "待映射岗位或目标岗位不存在", 404)
    row.resolved_job_id, row.resolved_at = job.id, now()
    existing = db.scalar(
        select(JobAlias).where(JobAlias.company_id == actor.company_id, JobAlias.platform == row.platform, JobAlias.normalized_alias == row.normalized_job_name)
    )
    if not existing:
        db.add(
            JobAlias(company_id=actor.company_id, job_id=job.id, platform=row.platform, raw_alias=row.raw_job_name, normalized_alias=row.normalized_job_name)
        )
    backfilled = RecruitmentCollaborationService(db).backfill_job_mapping(actor.company_id, row.platform, row.normalized_job_name, job)
    db.commit()
    return {**serialize(row), "backfilled_candidate_count": backfilled}


@router.post("/admin/jobs")
def create_job(body: JobCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = RecruitmentJob(company_id=actor.company_id, **body.model_dump())
    db.add(row)
    db.commit()
    return serialize(row)


@router.post("/admin/job-aliases")
def create_alias(body: AliasCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(RecruitmentJob, body.job_id)
    if not job or job.company_id != actor.company_id:
        raise ApplicationError("JOB_NOT_FOUND", "目标岗位不存在", 404)
    row = JobAlias(
        company_id=actor.company_id,
        job_id=body.job_id,
        platform=body.platform,
        raw_alias=body.raw_alias,
        normalized_alias=JobNameNormalizer().normalize(body.raw_alias),
    )
    db.add(row)
    db.commit()
    return serialize(row)


@router.post("/admin/recruiters")
def create_recruiter(body: RecruiterCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.role not in {"ADMIN", "HR_MANAGER", "RECRUITER"}:
        raise ApplicationError("INVALID_ROLE", "角色无效")
    row = Recruiter(company_id=actor.company_id, display_name=body.display_name, email=body.email, role=body.role, password_hash=hash_password(body.password))
    db.add(row)
    db.commit()
    data = serialize(row)
    data.pop("password_hash")
    return data


@router.post("/admin/accounts")
def create_account(body: AccountCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    recruiter = db.get(Recruiter, body.recruiter_id)
    if not recruiter or recruiter.company_id != actor.company_id:
        raise ApplicationError("RECRUITER_NOT_FOUND", "招聘者不存在", 404)
    row = RecruitmentAccount(company_id=actor.company_id, **body.model_dump())
    db.add(row)
    db.commit()
    return serialize(row)


@router.post("/admin/notifications/{notification_id}/{action}")
def notification_action(notification_id: str, action: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.get(NotificationOutbox, notification_id)
    if not row or row.company_id != actor.company_id:
        raise ApplicationError("NOTIFICATION_NOT_FOUND", "通知不存在", 404)
    if action == "retry":
        row.status, row.retry_count, row.next_retry_at = "PENDING", 0, now()
    elif action == "cancel" and row.status in {"PENDING", "FAILED"}:
        row.status = "CANCELLED"
    else:
        raise ApplicationError("ACTION_NOT_ALLOWED", "当前状态不允许此操作")
    db.commit()
    return {"success": True}


@router.get("/admin/settings")
def settings(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = serialize(_company_settings(db, actor.company_id))
    config = get_settings()
    row.update({"api_base_url": f"{config.public_web_url.rstrip('/')}/api/v1", "web_url": config.public_web_url, "environment": config.app_env})
    return row


@router.patch("/admin/settings")
def update_settings(body: RecruitmentSettingsUpdate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _company_settings(db, actor.company_id)
    before = serialize(row)
    if body.notify_on_contact is not None:
        row.notify_on_contact = body.notify_on_contact
    if body.catchup_enabled is not None:
        row.catchup_enabled = body.catchup_enabled
    db.commit()
    changed = {key: serialize(row)[key] for key in ("notify_on_contact", "catchup_enabled") if before.get(key) != serialize(row)[key]}
    if changed:
        db.add(AuditLog(company_id=actor.company_id, actor_id=actor.id, action="UPDATE_SETTINGS", entity_type="RecruitmentSetting", entity_id=row.id, after_json=changed))
        db.commit()
    return serialize(row)


def _company_settings(db: Session, company_id: str) -> RecruitmentSetting:
    row = db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == company_id))
    if row:
        return row
    row = RecruitmentSetting(company_id=company_id)
    db.add(row)
    db.commit()
    return row


@router.get("/plugin/settings")
def plugin_settings(actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _company_settings(db, actor.company_id)
    settings = get_settings()
    return {
        "catchup_enabled": row.catchup_enabled,
        "api_base_url": f"{settings.public_web_url.rstrip('/')}/api/v1",
        "web_url": settings.public_web_url,
        "environment": settings.app_env,
    }


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
