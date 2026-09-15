from __future__ import annotations

import hashlib
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from recruitment_collab.application.collaboration import ApplicationError, RecruitmentCollaborationService
from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.feishu import FeishuCallbackVerifier, FeishuIdentity, FeishuOAuthClient
from recruitment_collab.infrastructure.models import (
    AuditLog,
    BossAccountAssignment,
    BossDailyMetric,
    CandidateSource,
    CandidateSyncOutbox,
    Conflict,
    ConflictExclusion,
    ConversationScanCheckpoint,
    DuplicateLookupAlert,
    Engagement,
    FeishuBindingAttempt,
    FeishuBitableConfig,
    Interview,
    JobAlias,
    MockFeishuMessage,
    Notification,
    NotificationOutbox,
    PluginDevice,
    PluginDiagnostic,
    Recruiter,
    RecruiterAccessProfile,
    RecruitmentAccount,
    RecruitmentEvent,
    RecruitmentJob,
    RecruitmentSetting,
    SystemResetJob,
    UnmappedJob,
    WorkerHeartbeat,
    now,
)
from recruitment_collab.infrastructure.security import decode_token, hash_password, hash_token, make_token, verify_password

from .dependencies import ADMIN_SESSION_COOKIE, Actor, bootstrap_company_id, current_actor, plugin_actor, require_admin, workspace_state
from .schemas import (
    AccountCreate,
    AdminGrantRequest,
    AliasCreate,
    BossAccountAssignmentRequest,
    ContextResolveRequest,
    ConversationReconcileRequest,
    ConversationSyncRequest,
    DevLoginRequest,
    DiagnosticRequest,
    EventRequest,
    FeishuBindingStartRequest,
    FeishuDevicePollRequest,
    FeishuTableActivateRequest,
    FeishuTableValidateRequest,
    InterviewRequest,
    JobCreate,
    MessageSentRequest,
    ReasonRequest,
    RecruiterCreate,
    RecruitmentSettingsUpdate,
    ScanCheckpointRequest,
    SnapshotStatusRequest,
    SystemResetRequest,
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
    "boss-daily-metrics": BossDailyMetric,
}
ADMIN_EDITABLE_FIELDS = {
    # Role changes use the dedicated administrator grant/revoke APIs so role,
    # data scope and capabilities cannot drift apart.
    "recruiters": {"display_name", "email", "status", "password"},
    "accounts": {"recruiter_id", "platform", "platform_account_key", "account_display_name", "status"},
    "jobs": {"code", "canonical_name", "category", "status"},
    "candidate-sources": {"candidate_display_name", "candidate_age", "candidate_experience", "candidate_education", "recruitment_status", "status_evidence", "resume_status"},
    "interviews": {"scheduled_at", "duration_minutes", "location_type", "location_text", "status", "result", "notes", "job_id", "recruiter_id"},
}
ADMIN_DELETABLE_RESOURCES = {"accounts", "jobs", "job-aliases", "unmapped-jobs"}
SENSITIVE_COLUMNS = {"password_hash", "refresh_token_hash"}
# Technical identifiers and attachment/provider payloads are intentionally not
# sent to the browser. They are only needed by workers or privileged detail
# endpoints and must not become a general-purpose admin data export.
ADMIN_HIDDEN_COLUMNS = {
    "snapshot_tokens_json", "resume_tokens_json", "candidate_identity_signature",
    "conversation_job_key", "platform_candidate_id", "page_url_hash",
    "payload_json", "app_token", "candidate_table_id", "validation_json",
    "device_poll_token_hash", "state_hash",
}


def admin_serialize(row: Any) -> dict[str, Any]:
    data = serialize(row)
    for column in SENSITIVE_COLUMNS | ADMIN_HIDDEN_COLUMNS:
        data.pop(column, None)
    return data
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


def _operation_bindings(db: Session, company_id: str, recruiter_ids: set[str] | None = None) -> list[dict[str, Any]]:
    source_statement = (
        select(RecruitmentAccount.recruiter_id, func.count(CandidateSource.id))
        .join(CandidateSource, CandidateSource.platform_account_id == RecruitmentAccount.id)
        .where(RecruitmentAccount.company_id == company_id)
        .group_by(RecruitmentAccount.recruiter_id)
    )
    # "Bound" now means the administrator has assigned a BOSS account to this
    # Feishu member, not merely that the member logged in with Feishu.  Every
    # person in the app's contact scope gets a Feishu identity at web login,
    # so identity presence alone would mark the pending-assignment state as
    # bound and mislead both dashboards.
    assignments_by_recruiter: dict[str, int] = defaultdict(int)
    assignment_statement = (
        select(BossAccountAssignment.feishu_recruiter_id, func.count())
        .where(
            BossAccountAssignment.company_id == company_id,
            BossAccountAssignment.status == "ACTIVE",
            BossAccountAssignment.feishu_recruiter_id.is_not(None),
        )
        .group_by(BossAccountAssignment.feishu_recruiter_id)
    )
    if recruiter_ids:
        assignment_statement = assignment_statement.where(BossAccountAssignment.feishu_recruiter_id.in_(recruiter_ids))
    for recruiter_id, count in db.execute(assignment_statement).all():
        assignments_by_recruiter[recruiter_id] = int(count)
    if recruiter_ids:
        source_statement = source_statement.where(RecruitmentAccount.recruiter_id.in_(recruiter_ids))
    source_count_rows = db.execute(source_statement).all()
    source_counts: dict[str, int] = {recruiter_id: int(count) for recruiter_id, count in source_count_rows}
    devices_by_recruiter: dict[str, list[PluginDevice]] = defaultdict(list)
    device_statement = select(PluginDevice).where(PluginDevice.company_id == company_id).order_by(PluginDevice.updated_at.desc())
    if recruiter_ids:
        device_statement = device_statement.where(PluginDevice.recruiter_id.in_(recruiter_ids))
    for device in db.scalars(device_statement).all():
        devices_by_recruiter[device.recruiter_id].append(device)
    accounts_by_recruiter: dict[str, list[RecruitmentAccount]] = defaultdict(list)
    account_statement = select(RecruitmentAccount).where(
        RecruitmentAccount.company_id == company_id,
        RecruitmentAccount.status == "ACTIVE",
    )
    if recruiter_ids:
        account_statement = account_statement.where(RecruitmentAccount.recruiter_id.in_(recruiter_ids))
    for account in db.scalars(account_statement).all():
        accounts_by_recruiter[account.recruiter_id].append(account)
    checkpoints_by_account: dict[str, list[ConversationScanCheckpoint]] = defaultdict(list)
    for checkpoint in db.scalars(select(ConversationScanCheckpoint).where(ConversationScanCheckpoint.company_id == company_id)).all():
        checkpoints_by_account[checkpoint.account_display_name].append(checkpoint)

    bindings: list[dict[str, Any]] = []
    recruiters = db.scalars(select(Recruiter).where(Recruiter.company_id == company_id).order_by(Recruiter.display_name)).all()
    if recruiter_ids:
        recruiters = [recruiter for recruiter in recruiters if recruiter.id in recruiter_ids]
    for recruiter in recruiters:
        devices = devices_by_recruiter[recruiter.id]
        accounts = accounts_by_recruiter[recruiter.id]
        # In the personal console view a Feishu member without any BOSS
        # account is in the pending-assignment state: show the empty state
        # instead of a fake binding named after the member themselves.
        if recruiter_ids and not accounts:
            continue
        account_names = list(dict.fromkeys(account.account_display_name for account in accounts)) or [recruiter.display_name]
        checkpoints = [checkpoint for name in account_names for checkpoint in checkpoints_by_account[name]]
        bindings.append(
            {
                "recruiter_id": recruiter.id,
                "boss_accounts": account_names,
                "role": recruiter.role,
                "bound": assignments_by_recruiter[recruiter.id] > 0,
                "feishu_display_name": recruiter.feishu_display_name,
                # A revoked/deleted device is never active; stale credentials
                # are excluded so this reflects extensions seen recently.
                "active_device_count": sum(
                device.status == "ACTIVE" and _aware_utc(device.last_seen_at) >= now() - timedelta(minutes=15) for device in devices
                ),
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


def _recent_outbox_failures_for_actor(db: Session, actor: Actor) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for kind, model, condition in (
        ("candidate_sync", CandidateSyncOutbox, CandidateSyncOutbox.candidate_source_id.in_(select(CandidateSource.id).where(CandidateSource.platform_account_id.in_(_company_account_ids(db, actor))))),
        ("notification", NotificationOutbox, NotificationOutbox.recipient_recruiter_id == actor.id),
    ):
        rows = db.scalars(select(model).where(model.company_id == actor.company_id, condition, model.status.in_({"FAILED", "PENDING"})).order_by(model.updated_at.desc()).limit(20)).all()
        output.extend({"kind": kind, "id": row.id, "status": row.status, "retry_count": row.retry_count, "last_error": row.last_error, "updated_at": row.updated_at} for row in rows)  # type: ignore[attr-defined]
    return sorted(output, key=lambda item: str(item["updated_at"]), reverse=True)[:20]


def _recent_diagnostics_for_actor(db: Session, actor: Actor) -> list[dict[str, Any]]:
    rows = db.scalars(select(PluginDiagnostic).where(PluginDiagnostic.company_id == actor.company_id, PluginDiagnostic.recruiter_id == actor.id).order_by(PluginDiagnostic.created_at.desc()).limit(20)).all()
    return [
        {
            "id": row.id,
            "platform": row.platform,
            "adapter_version": row.adapter_version,
            "page_type": row.page_type,
            "account_status": row.account_status,
            "candidate_status": row.candidate_status,
            "job_status": row.job_status,
            "platform_id_status": row.platform_id_status,
            "created_at": row.created_at,
            "error_codes": row.error_codes_json,
            "context": row.sanitized_context_json,
        }
        for row in rows
    ]


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _epoch(value: datetime | None) -> float:
    return _aware_utc(value).timestamp() if value else float("-inf")


def _calendar_key(value: datetime) -> int:
    """Day identity for BOSS rows that only render a calendar label."""
    local = _aware_utc(value)
    return local.year * 10_000 + local.month * 100 + local.day


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
    setting = db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == actor.company_id))
    if setting and setting.reset_in_progress:
        raise ApplicationError("SYSTEM_RESET_IN_PROGRESS", "系统正在重新初始化同步，请稍后重新绑定扩展", 503)
    display_name = (account_display_name or "").strip()
    mapped = db.scalar(
        select(RecruitmentAccount.id).where(
            RecruitmentAccount.company_id == actor.company_id,
            RecruitmentAccount.account_display_name == display_name,
            RecruitmentAccount.status == "ACTIVE",
        )
    )
    if mapped:
        assignment = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == mapped, BossAccountAssignment.status == "ACTIVE"))
        if assignment and assignment.feishu_recruiter_id == actor.id:
            return actor.company_id
        raise ApplicationError("BOSS_ACCOUNT_FORBIDDEN", "当前飞书账号未被分配这个 BOSS 招聘账号", 403)
    recruiter = db.get(Recruiter, actor.id)
    bound_feishu_name = recruiter.feishu_display_name if recruiter else None
    if actor.display_name != display_name and display_name != bound_feishu_name:
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
def me(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _access_profile(db, actor)
    assignment_count = db.scalar(
        select(func.count())
        .select_from(BossAccountAssignment)
        .where(BossAccountAssignment.company_id == actor.company_id, BossAccountAssignment.feishu_recruiter_id == actor.id, BossAccountAssignment.status == "ACTIVE")
    )
    return {
        **actor.__dict__,
        "workspace": workspace_state(db, actor),
        # Drives the pending-assignment state in the console: a member only
        # leaves "待分配" once an administrator assigns them a BOSS account.
        "has_boss_assignment": bool(assignment_count),
        "capabilities": {
            "data_scope": profile.data_scope,
            "can_manage_team": profile.can_manage_team,
            "can_manage_feishu": profile.can_manage_feishu,
            "can_manage_jobs": profile.can_manage_jobs,
            "can_reset_system": profile.can_reset_system,
        },
    }


@router.get("/plugin/me")
def plugin_me(actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, str]:
    """Return the bound Feishu display name used as the sync recruiter."""
    recruiter = db.get(Recruiter, actor.id)
    return {"display_name": recruiter.feishu_display_name if recruiter and recruiter.feishu_display_name else actor.display_name, "role": actor.role}


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
    if not device or (device.recruiter_id != actor.id and not _access_profile(db, actor).can_manage_team):
        raise ApplicationError("DEVICE_NOT_FOUND", "设备不存在", 404)
    device.status, device.revoked_at = "REVOKED", now()
    db.commit()
    return {"success": True}


@router.post("/plugin/device/logout")
def plugin_device_logout(actor: Actor = Depends(plugin_actor), authorization: str = Header(default=""), db: Session = Depends(get_db)) -> dict[str, bool]:
    """Delete the extension device record when the user explicitly logs out."""
    try:
        payload = decode_token(authorization[7:] if authorization.startswith("Bearer ") else "", "access")
    except jwt.PyJWTError as exc:
        raise ApplicationError("INVALID_TOKEN", "登录已失效", 401) from exc
    device_id = str(payload.get("device_id") or "").strip()
    if device_id:
        device = db.scalar(select(PluginDevice).where(PluginDevice.device_id == device_id, PluginDevice.recruiter_id == actor.id))
        if device:
            db.delete(device)
            db.commit()
    return {"success": True}


@router.get("/plugin/feishu-binding/status")
def feishu_binding_status(
    account_display_name: str = Query(min_length=1, max_length=100), actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, account_display_name, db)
    account = db.scalar(select(RecruitmentAccount).where(RecruitmentAccount.company_id == company_id, RecruitmentAccount.account_display_name == account_display_name.strip(), RecruitmentAccount.status == "ACTIVE"))
    assignment = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE")) if account else None
    return {
        "account_display_name": account_display_name.strip(),
        "bound": bool(assignment and (not actor.id or assignment.feishu_recruiter_id == actor.id)),
        "feishu_display_name": assignment.feishu_display_name if assignment else None,
    }


@router.post("/plugin/feishu-binding/start")
def feishu_binding_start(body: FeishuBindingStartRequest, company_id: str = Depends(bootstrap_company_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if settings.feishu_mode != "real":
        raise ApplicationError("FEISHU_BINDING_UNAVAILABLE", "当前未启用真实飞书模式", 503)
    display_name = body.account_display_name.strip()
    # A first-time extension bind may legitimately be the first place where
    # this BOSS account is seen.  The actual identity binding still happens
    # only after the user completes Feishu OAuth in _complete_boss_binding;
    # creating the placeholder here avoids rejecting new Windows accounts
    # before they can authenticate.
    recruiter = RecruitmentCollaborationService(db).page_recruiter(
        company_id, display_name, create=body.action == "bind"
    )
    if get_settings().app_env == "production" and body.action == "bind" and not body.device_id:
        raise ApplicationError("DEVICE_ID_REQUIRED", "绑定扩展必须提供浏览器设备标识", 400)
    if body.action == "unbind" and not recruiter:
        raise ApplicationError("FEISHU_NOT_BOUND", "当前 BOSS 招聘账号尚未分配负责人", 409)
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


@router.post("/plugin/company-daily-data/login/start")
def company_daily_login_start(body: FeishuBindingStartRequest, company_id: str = Depends(bootstrap_company_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    from recruitment_collab.infrastructure.models import BossCompanyDailyConfig
    config = db.scalar(select(BossCompanyDailyConfig).where(BossCompanyDailyConfig.company_id == company_id, BossCompanyDailyConfig.enabled.is_(True)))
    if not config or config.collector_name != body.account_display_name.strip() or not body.device_id:
        raise ApplicationError("DAILY_COLLECTOR_REQUIRED", "请先打开已配置的公司日报管理员 BOSS 沟通页", 403)
    if get_settings().feishu_mode != "real":
        raise ApplicationError("FEISHU_BINDING_UNAVAILABLE", "当前未启用真实飞书模式", 503)
    state, poll_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    attempt = FeishuBindingAttempt(company_id=company_id, account_display_name=config.collector_name,
        action="company_daily_login", state_hash=hash_token(state), expires_at=now() + timedelta(minutes=10),
        device_id=body.device_id, device_name=body.device_name, device_poll_token_hash=hash_token(poll_token))
    db.add(attempt)
    db.commit()
    return {"authorization_url": FeishuOAuthClient(get_settings()).authorization_url(state), "attempt_id": attempt.id, "poll_token": poll_token, "expires_in": 600}


def _complete_company_daily_login(db: Session, attempt: FeishuBindingAttempt, identity: FeishuIdentity) -> HTMLResponse:
    recruiter = db.scalar(select(Recruiter).where(Recruiter.company_id == attempt.company_id, Recruiter.feishu_open_id == identity.open_id, Recruiter.status == "ACTIVE", Recruiter.role == "ADMIN"))
    if not recruiter:
        raise ApplicationError("DAILY_ADMIN_REQUIRED", "公司日报专用登录仅限本系统管理员；不会自动提升权限或更换 BOSS 负责人", 403)
    attempt.recruiter_id, attempt.status = recruiter.id, "APPROVED"
    db.add(AuditLog(company_id=attempt.company_id, actor_id=recruiter.id, action="COMPANY_DAILY_DEVICE_APPROVED", entity_type="PluginDevice", entity_id=attempt.device_id, after_json={"collector": attempt.account_display_name}))
    db.commit()
    return HTMLResponse("<!doctype html><meta charset='utf-8'><title>公司日报授权成功</title><h2>公司日报授权成功</h2><p>现有 BOSS 负责人和候选人归属未改变。请回到扩展查看同步状态。</p>")


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
    if attempt.action == "company_daily_login":
        return _complete_company_daily_login(db, attempt, identity)
    return _complete_web_login(db, attempt, identity) if attempt.action == "web_login" else _complete_boss_binding(db, attempt, identity)


def _pending_binding_attempt(db: Session, state: str) -> FeishuBindingAttempt:
    # Serialize callback consumption. Without a row lock two browser retries
    # could both observe PENDING and bind/issue sessions twice.
    statement = select(FeishuBindingAttempt).where(FeishuBindingAttempt.state_hash == hash_token(state))
    if db.bind and db.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    attempt = db.scalar(statement)
    if not attempt or attempt.status != "PENDING":
        raise ApplicationError("FEISHU_BINDING_STATE_INVALID", "飞书绑定请求无效或已使用", 400)
    if _aware_utc(attempt.expires_at) < datetime.now(timezone.utc):
        attempt.status = "EXPIRED"
        db.commit()
        raise ApplicationError("FEISHU_BINDING_STATE_EXPIRED", "飞书绑定请求已过期，请回到扩展重新发起", 400)
    return attempt


def _complete_web_login(db: Session, attempt: FeishuBindingAttempt, identity: FeishuIdentity) -> Response:
    recruiter = db.scalar(
        select(Recruiter).where(Recruiter.company_id == attempt.company_id, Recruiter.feishu_open_id == identity.open_id)
    )
    if recruiter and recruiter.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "该招聘账号已停用，请联系管理员", 403)
    if not recruiter:
        # Every person in the Feishu app's published contact scope may enter
        # the console.  Assignment controls data scope and extension access,
        # not basic authentication.  Create a minimal identity record so the
        # person can see the pending-assignment state and download the
        # extension; no BOSS account or candidate data is attached yet.
        identity_key = hashlib.sha256(f"{attempt.company_id}|{identity.open_id}".encode()).hexdigest()
        recruiter = Recruiter(
            company_id=attempt.company_id,
            display_name=identity.display_name.strip() or "飞书成员",
            feishu_open_id=identity.open_id,
            feishu_user_id=identity.user_id,
            feishu_display_name=identity.display_name.strip() or "飞书成员",
            email=f"feishu-{identity_key[:24]}@identity.invalid",
            password_hash="FEISHU_IDENTITY_CANNOT_PASSWORD_LOGIN",
            role="RECRUITER",
            status="ACTIVE",
        )
        db.add(recruiter)
        db.flush()
        db.add(RecruiterAccessProfile(company_id=attempt.company_id, recruiter_id=recruiter.id, data_scope="OWN"))
    recruiter.feishu_user_id = identity.user_id or recruiter.feishu_user_id
    recruiter.feishu_display_name = identity.display_name.strip() or recruiter.feishu_display_name
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
    account_name = attempt.account_display_name
    account_statement = select(RecruitmentAccount).where(
        RecruitmentAccount.company_id == attempt.company_id,
        RecruitmentAccount.account_display_name == account_name,
        RecruitmentAccount.status == "ACTIVE",
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        account_statement = account_statement.with_for_update()
    account = db.scalar(account_statement)
    if not account:
        raise ApplicationError("BOSS_ACCOUNT_NOT_FOUND", "未识别当前 BOSS 招聘账号，请回到沟通页重新发起绑定", 404)
    recruiter = db.scalar(
        select(Recruiter).where(
            Recruiter.company_id == attempt.company_id,
            Recruiter.feishu_open_id == identity.open_id,
        )
    )
    if recruiter and recruiter.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "该飞书账号已停用，请联系管理员", 403)
    if not recruiter:
        recruiter = _feishu_identity_recruiter(db, attempt.company_id, identity.open_id, identity.user_id, identity.display_name)
        db.add(RecruiterAccessProfile(company_id=attempt.company_id, recruiter_id=recruiter.id, data_scope="OWN"))
    assignment = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE")) if account else None
    if assignment and assignment.feishu_recruiter_id != recruiter.id:
        raise ApplicationError("BOSS_ACCOUNT_ASSIGNED_TO_OTHER", "这个 BOSS 账号已分配给其他飞书成员，请联系管理员替换负责人", 403)
    if not assignment:
        occupied = db.scalar(
            select(BossAccountAssignment).where(
                BossAccountAssignment.company_id == attempt.company_id,
                BossAccountAssignment.feishu_recruiter_id == recruiter.id,
                BossAccountAssignment.status == "ACTIVE",
            )
        )
        if occupied:
            raise ApplicationError("FEISHU_ALREADY_ASSIGNED", "你的飞书账号已绑定其他 BOSS 账号，如需更换请联系管理员", 409)
        assignment = BossAccountAssignment(
            company_id=attempt.company_id,
            boss_account_id=account.id,
            feishu_recruiter_id=recruiter.id,
            feishu_open_id=identity.open_id,
            feishu_display_name=identity.display_name.strip() or recruiter.display_name,
            assignment_version=1,
            assigned_at=now(),
        )
        db.add(assignment)
        account.recruiter_id = recruiter.id
        db.add(
            AuditLog(
                company_id=attempt.company_id,
                actor_id=recruiter.id,
                action="BOSS_ACCOUNT_SELF_ASSIGNED",
                entity_type="RecruitmentAccount",
                entity_id=account.id,
                after_json={"feishu_open_id": identity.open_id, "feishu_display_name": identity.display_name},
            )
        )
    recruiter.feishu_user_id = identity.user_id or recruiter.feishu_user_id
    recruiter.feishu_display_name = identity.display_name.strip() or recruiter.feishu_display_name
    if attempt.action == "bind":
        attempt.recruiter_id = recruiter.id
        detail = f"{identity.display_name} 已连接 BOSS 账号 {account_name}，以后无需重复绑定"
        title = "扩展登录成功"
    else:
        for device in db.scalars(select(PluginDevice).where(PluginDevice.recruiter_id == recruiter.id, PluginDevice.device_id == attempt.device_id)).all():
            device.status, device.revoked_at, device.refresh_token_hash = "REVOKED", now(), None
        title, detail = "扩展已退出", f"这台浏览器已停止同步 {account_name}"
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
        "user": {"display_name": recruiter.feishu_display_name or recruiter.display_name, "boss_account_name": attempt.account_display_name},
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


@router.post("/plugin/boss-daily-metrics")
def boss_daily_metrics(body: dict[str, Any], actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Accept the six counters visible in BOSS's daily company dashboard.

    The extension sends only labelled numeric counters and the selected date;
    no HTML or screenshot is persisted. Repeated reads for the same account and
    date update the row, making the midnight run idempotent.
    """
    if not get_settings().is_development:
        raise ApplicationError("DAILY_EXTENSION_UPGRADE_REQUIRED", "请更新扩展后同步公司日报", 410)
    account_name = str(body.get("account_display_name") or "").strip()
    metric_date = str(body.get("metric_date") or "").strip()
    if not account_name or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", metric_date):
        raise ApplicationError("DAILY_METRIC_INVALID", "缺少 BOSS 账号或有效日期", 400)
    account = db.scalar(select(RecruitmentAccount).where(RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.account_display_name == account_name, RecruitmentAccount.status == "ACTIVE"))
    if not account or (not _has_company_data_scope(db, actor) and account.id not in db.scalars(_company_account_ids(db, actor)).all()):
        raise ApplicationError("BOSS_ASSIGNMENT_REQUIRED", "当前飞书账号未分配该 BOSS 账号", 403)
    allowed = {"boss_viewed_talent", "boss_started_chat", "boss_communication", "talent_viewed_boss", "talent_started_chat"}
    values = {key: max(0, min(int(body.get(key, 0) or 0), 10_000_000)) for key in allowed}
    row = db.scalar(select(BossDailyMetric).where(BossDailyMetric.company_id == actor.company_id, BossDailyMetric.boss_account_id == account.id, BossDailyMetric.metric_date == metric_date))
    if not row:
        row = BossDailyMetric(company_id=actor.company_id, boss_account_id=account.id, metric_date=metric_date, boss_name=account_name, **values)
        db.add(row)
    else:
        for key, value in values.items(): setattr(row, key, value)
        row.boss_name = account_name; row.source_observed_at = now()
    db.commit()
    return {"id": row.id, "metric_date": row.metric_date, "boss_account": row.boss_name, **values, "status": "SAVED"}


@router.post("/plugin/conversations/sync")
def sync_conversation(body: ConversationSyncRequest, actor: Actor = Depends(plugin_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    if _ignored_boss_page(body):
        raise ApplicationError("UNSUPPORTED_PAGE", "仅候选人沟通页可同步会话")
    if _aware_utc(body.sent_at) > datetime.now(timezone.utc) + timedelta(hours=24):
        raise ApplicationError("EVENT_TIME_INVALID", "会话时间不能晚于服务器时间", 400)
    return RecruitmentCollaborationService(db).sync_candidate_observation(company_id, _validated_plugin_payload(body))


@router.get("/plugin/conversations/index")
def get_conversation_index(
    account_display_name: str = Query(min_length=1, max_length=100),
    platform: str = "boss",
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return this BOSS account's candidate anchors for list-first polling.

    The extension reads the BOSS list row's own timestamp and asks this
    endpoint whether that row is already known and current.  Rows that are
    absent, newer, or still missing their Feishu row must be opened and
    reconciled; everything else is skipped without opening a chat.
    No chat text, HTML, cookie or screenshot bytes are returned here.
    """
    company_id = plugin_company_for_account(actor, account_display_name, db)
    account = db.scalar(
        select(RecruitmentAccount).where(
            RecruitmentAccount.company_id == company_id,
            RecruitmentAccount.platform == platform,
            RecruitmentAccount.account_display_name == account_display_name.strip(),
        )
    )
    if not account:
        return {"items": []}
    rows = db.scalars(
        select(CandidateSource).where(
            CandidateSource.company_id == company_id,
            CandidateSource.platform == platform,
            CandidateSource.platform_account_id == account.id,
        )
    ).all()
    return {
        "items": [
            {
                "candidate_source_id": row.id,
                "candidate_display_name": row.candidate_display_name,
                "job_display_name": row.raw_job_name,
                "conversation_updated_at": _aware_utc(row.conversation_updated_at or row.created_at).isoformat(),
                "created_at": _aware_utc(row.created_at).isoformat(),
                "synced": row.feishu_record_id is not None,
                "recruiter_account": account_display_name.strip(),
            }
            for row in rows
            if row.conversation_updated_at is not None
        ]
    }


def _indexed_candidate_anchors(
    company_id: str,
    platform: str,
    account: RecruitmentAccount,
    db: Session,
) -> dict[tuple[str, str], CandidateSource]:
    """Map (normalized name, normalized job) to the newest indexed source row."""
    rows = db.scalars(
        select(CandidateSource).where(
            CandidateSource.company_id == company_id,
            CandidateSource.platform == platform,
            CandidateSource.platform_account_id == account.id,
        )
    ).all()
    anchors: dict[tuple[str, str], CandidateSource] = {}
    name_normalizer, job_normalizer = CandidateNameNormalizer(), JobNameNormalizer()
    for row in rows:
        key = (name_normalizer.normalize(row.candidate_display_name), job_normalizer.normalize(row.raw_job_name))
        existing = anchors.get(key)
        if existing is None or _epoch(row.conversation_updated_at) > _epoch(existing.conversation_updated_at):
            anchors[key] = row
    return anchors


@router.post("/plugin/conversations/reconcile")
def reconcile_conversation(
    body: ConversationReconcileRequest,
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Decide one BOSS list row against the stored table without opening it.

    This is the whole historical-scan rule, evaluated per row instead of
    against an account-wide watermark:

    * candidate absent from the table            -> ``SYNC`` (create the row)
    * present, but BOSS list time is newer       -> ``SYNC`` (update the row)
    * present with the same BOSS list time       -> ``SKIP``  (nothing changed)
    * present but its Feishu row was never sent  -> ``SYNC``  (finish the row)

    BOSS only renders a clock for today/yesterday, so a row showing a bare
    calendar label carries no hour.  Those rows are compared by calendar day
    exactly like the reader sees them, never as midnight against a precise
    stored timestamp.
    """
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    account_name = body.account_display_name.strip()
    account = db.scalar(
        select(RecruitmentAccount).where(
            RecruitmentAccount.company_id == company_id,
            RecruitmentAccount.platform == body.platform,
            RecruitmentAccount.account_display_name == account_name,
        )
    )
    if not account:
        raise ApplicationError("BOSS_ACCOUNT_NOT_FOUND", "当前 BOSS 账号尚未建立映射", 404)
    anchors = _indexed_candidate_anchors(company_id, body.platform, account, db)
    key = (
        CandidateNameNormalizer().normalize(body.candidate_display_name),
        JobNameNormalizer().normalize(body.job_display_name),
    )
    known = anchors.get(key)
    list_activity = _aware_utc(body.list_activity_at)
    if known is None:
        # Never seen here: index it. This deliberately mirrors the click path,
        # which records the row without asserting recruiter outbound evidence.
        # A candidate the reader has actually messaged is already in the table
        # (message-sent always creates and queues), so this cannot strand one.
        return {"decision": "SYNC", "reason": "CANDIDATE_NOT_IN_TABLE", "known_updated_at": None}
    known_updated_at = known.conversation_updated_at or known.created_at
    if body.list_activity_is_date_only:
        newer = _calendar_key(list_activity) > _calendar_key(known_updated_at)
    else:
        newer = _epoch(list_activity) > _epoch(known_updated_at)
    if newer:
        decision, reason = "SYNC", "LIST_ACTIVITY_NEWER"
    elif known.feishu_record_id is None:
        # The row's BOSS time is unchanged, but its Feishu row was never
        # written (e.g. indexed before this rule existed). Finish it now.
        decision, reason = "SYNC", "FEISHU_ROW_MISSING"
    else:
        decision, reason = "SKIP", "LIST_ACTIVITY_UNCHANGED"
    return {
        "decision": decision,
        "reason": reason,
        "candidate_source_id": known.id,
        "known_updated_at": _aware_utc(known_updated_at).isoformat(),
    }


@router.post("/plugin/conversations/scan-report")
def report_scan_completed(
    body: ScanCheckpointRequest,
    actor: Actor = Depends(plugin_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record a finished poll pass for operations monitoring.

    Historical scanning is now per-row reconciliation, so this value never
    filters or anchors anything: it only feeds the dashboard's "最近同步"
    reading for each BOSS account.
    """
    company_id = plugin_company_for_account(actor, body.account_display_name, db)
    reported_at = _aware_utc(body.completed_through_at)
    if reported_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ApplicationError("CHECKPOINT_TIME_INVALID", "扫描完成时间不能晚于服务器时间", 400)
    account_name = body.account_display_name.strip()
    row = db.scalar(
        select(ConversationScanCheckpoint).where(
            ConversationScanCheckpoint.company_id == company_id,
            ConversationScanCheckpoint.platform == body.platform,
            ConversationScanCheckpoint.account_display_name == account_name,
        )
    )
    if row is None:
        row = ConversationScanCheckpoint(
            company_id=company_id,
            platform=body.platform,
            account_display_name=account_name,
            completed_through_at=reported_at,
            cursor_json=body.cursor,
        )
        db.add(row)
    else:
        row.completed_through_at = reported_at
        row.cursor_json = body.cursor
    db.commit()
    return {"completed_through_at": reported_at.isoformat(), "accepted": True}


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
    client = _bitable_client(db, actor.company_id)
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
    try:
        token = await run_in_threadpool(_bitable_client(db, actor.company_id).upload_resume, safe_name, content, content_type)
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
    statement = _scope_statement("conflicts", select(Conflict).where(Conflict.company_id == actor.company_id), actor, db)
    return [serialize(row) for row in db.scalars(statement.order_by(Conflict.created_at.desc())).all()]


@router.get("/conflicts/{conflict_id}")
def conflict_detail(conflict_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id or not _row_visible_to_actor("conflicts", row, actor, db):
        raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    return serialize(row)


@router.post("/conflicts/{conflict_id}/exclude")
def exclude(conflict_id: str, body: ReasonRequest, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.get(Conflict, conflict_id)
    if not row or row.company_id != actor.company_id or not _row_visible_to_actor("conflicts", row, actor, db):
        raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
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
    if not row or row.company_id != actor.company_id or not _row_visible_to_actor("conflicts", row, actor, db):
        raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
    if action == "transfer" and not _access_profile(db, actor).can_manage_team:
        raise ApplicationError("FORBIDDEN", "仅管理员可执行转交", 403)
    row.status = mapping[action]
    row.resolution = body.reason
    db.commit()
    return {"success": True}


@router.get("/admin/overview")
def overview(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    def count(model: Any, *conditions: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    own_accounts = _company_account_ids(db, actor)
    source_ownership = CandidateSource.platform_account_id.in_(own_accounts)
    own_recruiter = not _has_company_data_scope(db, actor)
    message_conditions = [RecruitmentEvent.company_id == actor.company_id, RecruitmentEvent.event_type == "MESSAGE_SENT"]
    if own_recruiter:
        message_conditions.append(
            RecruitmentEvent.candidate_source_id.in_(
                select(CandidateSource.id).where(CandidateSource.platform_account_id.in_(own_accounts))
            )
        )
    return {
        "candidate_queries": count(CandidateSource, CandidateSource.company_id == actor.company_id, *( [source_ownership] if own_recruiter else [])),
        "engagements": count(Engagement, Engagement.company_id == actor.company_id, *([Engagement.recruiter_id == actor.id] if own_recruiter else [])),
        "open_conflicts": count(Conflict, Conflict.company_id == actor.company_id, Conflict.status == "OPEN", *([((Conflict.left_recruiter_id == actor.id) | (Conflict.right_recruiter_id == actor.id))] if own_recruiter else [])),
        "interviews": count(Interview, Interview.company_id == actor.company_id, Interview.status == "SCHEDULED", *([Interview.recruiter_id == actor.id] if own_recruiter else [])),
        "unmapped_jobs": count(UnmappedJob, UnmappedJob.company_id == actor.company_id, UnmappedJob.resolved_job_id.is_(None)),
        "notification_failures": count(NotificationOutbox, NotificationOutbox.company_id == actor.company_id, NotificationOutbox.status == "FAILED", *([NotificationOutbox.recipient_recruiter_id == actor.id] if own_recruiter else [])),
        "message_sent": count(RecruitmentEvent, *message_conditions),
    }


@router.get("/admin/operations")
def operations(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    current = now()
    own = not _has_company_data_scope(db, actor)
    account_ids = _company_account_ids(db, actor)
    source_ids = select(CandidateSource.id).where(CandidateSource.company_id == actor.company_id, CandidateSource.platform_account_id.in_(account_ids))
    sync_statement = select(CandidateSyncOutbox.status, func.count()).where(CandidateSyncOutbox.company_id == actor.company_id)
    notification_statement = select(NotificationOutbox.status, func.count()).where(NotificationOutbox.company_id == actor.company_id)
    if own:
        sync_statement = sync_statement.where(CandidateSyncOutbox.candidate_source_id.in_(source_ids))
        notification_statement = notification_statement.where(NotificationOutbox.recipient_recruiter_id == actor.id)
    candidate_sync = {str(status): int(count) for status, count in db.execute(sync_statement.group_by(CandidateSyncOutbox.status)).all()}
    notifications = {str(status): int(count) for status, count in db.execute(notification_statement.group_by(NotificationOutbox.status)).all()}
    snapshot_statement = (
        select(CandidateSource.snapshot_status, func.count()).where(CandidateSource.company_id == actor.company_id).group_by(CandidateSource.snapshot_status)
    )
    if own:
        snapshot_statement = snapshot_statement.where(CandidateSource.platform_account_id.in_(account_ids))
    snapshot_counts = {str(status): int(count) for status, count in db.execute(snapshot_statement).all()}
    source_count_statement = select(func.count()).select_from(CandidateSource).where(CandidateSource.company_id == actor.company_id)
    if own:
        source_count_statement = source_count_statement.where(CandidateSource.platform_account_id.in_(account_ids))
    source_count = int(db.scalar(source_count_statement) or 0)
    checkpoint_statement = select(func.count()).select_from(ConversationScanCheckpoint).where(ConversationScanCheckpoint.company_id == actor.company_id)
    if own:
        checkpoint_statement = checkpoint_statement.where(ConversationScanCheckpoint.account_display_name.in_(select(RecruitmentAccount.account_display_name).where(RecruitmentAccount.recruiter_id == actor.id)))
    checkpoint_count = int(db.scalar(checkpoint_statement) or 0)
    lookup_statement = select(func.count()).select_from(DuplicateLookupAlert).where(DuplicateLookupAlert.company_id == actor.company_id)
    if own:
        lookup_statement = lookup_statement.where((DuplicateLookupAlert.viewer_recruiter_id == actor.id) | (DuplicateLookupAlert.matched_recruiter_id == actor.id))
    lookup_alert_count = int(db.scalar(lookup_statement) or 0)
    workers, alerts = _worker_operations(db, current)
    queue_alerts, queue_values = _queue_alerts(candidate_sync, notifications, snapshot_counts, source_count, checkpoint_count)
    alerts.extend(queue_alerts)
    bindings = _operation_bindings(db, actor.company_id, {actor.id} if own else None)
    communication_summary = _communication_summary(db, actor)
    message_sent_total = sum(item["message_count"] for item in communication_summary)
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
            "message_sent_total": message_sent_total,
        },
        "communication_summary": communication_summary,
        "workers": workers,
        "queues": {"candidate_sync": candidate_sync, "notifications": notifications, "snapshots": snapshot_counts},
        "bindings": bindings,
        "recent_failures": _recent_outbox_failures(db, actor.company_id) if not own else _recent_outbox_failures_for_actor(db, actor),
        "diagnostics": _recent_diagnostics(db, actor.company_id) if not own else _recent_diagnostics_for_actor(db, actor),
        "configuration": {
            "environment": settings.app_env,
            "feishu_mode": settings.feishu_mode,
            "feishu_app_configured": bool(settings.feishu_app_id and settings.feishu_app_secret),
            "feishu_candidate_table_configured": bool(_active_bitable(db, actor.company_id) or (settings.feishu_bitable_app_token and settings.feishu_bitable_candidate_table_id)),
            "retention_days": {
                "candidate": settings.candidate_cache_days,
                "diagnostic": settings.diagnostic_retention_days,
                "event": settings.event_retention_days,
                "audit": settings.audit_retention_days,
            },
            "retention_run_interval_days": settings.retention_run_interval_days,
        },
    }


def _extension_release() -> tuple[Path, dict[str, Any]]:
    settings = get_settings()
    package = Path(settings.extension_package_path).resolve()
    metadata_path = Path(settings.extension_release_metadata_path).resolve()
    if not package.is_file() or not metadata_path.is_file():
        raise ApplicationError("EXTENSION_RELEASE_UNAVAILABLE", "扩展安装包暂不可用", 503)
    try:
        import json
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApplicationError("EXTENSION_RELEASE_INVALID", "扩展发布信息暂不可用", 503) from exc
    if not isinstance(metadata, dict) or not metadata.get("version"):
        raise ApplicationError("EXTENSION_RELEASE_INVALID", "扩展发布信息暂不可用", 503)
    return package, metadata


def _access_profile(db: Session, actor: Actor) -> RecruiterAccessProfile:
    profile = db.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.recruiter_id == actor.id, RecruiterAccessProfile.company_id == actor.company_id))
    if profile:
        return profile
    # Compatibility for databases upgraded before the access-profile migration.
    # Existing administrators retain the legacy company-wide scope until the
    # migration backfills an explicit profile; new identities remain own-data.
    company_scope = actor.role.upper() == "ADMIN"
    return RecruiterAccessProfile(
        company_id=actor.company_id,
        recruiter_id=actor.id,
        data_scope="COMPANY" if company_scope else "OWN",
        can_manage_team=company_scope,
        can_manage_feishu=company_scope,
        can_manage_jobs=company_scope,
        can_reset_system=company_scope,
    )


def _has_company_data_scope(db: Session, actor: Actor) -> bool:
    """Return the single, explicit company-wide boundary used by reads.

    A role administrator is authoritative.  A fully-enabled legacy profile
    is supported, but partial capability combinations never broaden data
    visibility beyond the recruiter's own BOSS account.
    """
    profile = _access_profile(db, actor)
    return actor.role.upper() == "ADMIN" or bool(
        profile.data_scope == "COMPANY"
        and profile.can_manage_team
        and profile.can_manage_feishu
        and profile.can_manage_jobs
        and profile.can_reset_system
    )


def _require_capability(db: Session, actor: Actor, capability: str) -> RecruiterAccessProfile:
    profile = _access_profile(db, actor)
    if not getattr(profile, capability, False):
        raise ApplicationError("FORBIDDEN", "当前账号没有执行此操作的权限", 403)
    return profile


def _active_bitable(db: Session, company_id: str) -> FeishuBitableConfig | None:
    return db.scalar(select(FeishuBitableConfig).where(FeishuBitableConfig.company_id == company_id, FeishuBitableConfig.status == "ACTIVE"))


def _company_account_ids(db: Session, actor: Actor):
    return select(RecruitmentAccount.id).where(RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.recruiter_id == actor.id)


def _communication_summary(db: Session, actor: Actor) -> list[dict[str, Any]]:
    """Aggregate confirmed outbound BOSS messages by recruiter and account.

    ``MESSAGE_SENT`` is emitted only after the extension observes a successful
    recruiter-side send.  Keeping this report derived from the event ledger
    means it is independent of Feishu table delivery and remains correct when
    a BOSS account is reassigned to another Feishu member.
    """
    account_ids = _company_account_ids(db, actor)
    statement = (
        select(
            RecruitmentEvent.recruiter_id,
            Recruiter.display_name,
            Recruiter.feishu_display_name,
            RecruitmentAccount.id.label("account_id"),
            RecruitmentAccount.account_display_name,
            func.count(RecruitmentEvent.id).label("message_count"),
            func.count(func.distinct(RecruitmentEvent.candidate_source_id)).label("candidate_count"),
            func.max(RecruitmentEvent.event_time).label("last_message_at"),
        )
        .join(CandidateSource, CandidateSource.id == RecruitmentEvent.candidate_source_id)
        .join(RecruitmentAccount, RecruitmentAccount.id == CandidateSource.platform_account_id)
        .join(Recruiter, Recruiter.id == RecruitmentEvent.recruiter_id)
        .where(
            RecruitmentEvent.company_id == actor.company_id,
            RecruitmentEvent.event_type == "MESSAGE_SENT",
        )
    )
    if not _has_company_data_scope(db, actor):
        # A reassigned Feishu owner inherits the BOSS account's history. Scope
        # by account rather than event recruiter so the new owner can still
        # see the prior conversation totals.
        statement = statement.where(CandidateSource.platform_account_id.in_(account_ids))
    rows = db.execute(
        statement.group_by(
            RecruitmentEvent.recruiter_id,
            Recruiter.display_name,
            Recruiter.feishu_display_name,
            RecruitmentAccount.id,
            RecruitmentAccount.account_display_name,
        ).order_by(func.max(RecruitmentEvent.event_time).desc())
    ).all()
    return [
        {
            "recruiter_id": recruiter_id,
            "recruiter_name": feishu_display_name or display_name,
            "boss_account_id": account_id,
            "boss_account": account_display_name,
            "message_count": int(message_count or 0),
            "candidate_count": int(candidate_count or 0),
            "last_message_at": last_message_at,
        }
        for recruiter_id, display_name, feishu_display_name, account_id, account_display_name, message_count, candidate_count, last_message_at in rows
    ]


def _candidate_message_counts(db: Session, source_ids: list[str]) -> dict[str, tuple[int, datetime | None]]:
    if not source_ids:
        return {}
    rows = db.execute(
        select(
            RecruitmentEvent.candidate_source_id,
            func.count(RecruitmentEvent.id),
            func.max(RecruitmentEvent.event_time),
        )
        .where(
            RecruitmentEvent.candidate_source_id.in_(source_ids),
            RecruitmentEvent.event_type == "MESSAGE_SENT",
        )
        .group_by(RecruitmentEvent.candidate_source_id)
    ).all()
    return {source_id: (int(count or 0), last_message_at) for source_id, count, last_message_at in rows}


def _scope_statement(resource: str, statement: Any, actor: Actor, db: Session) -> Any:
    if _has_company_data_scope(db, actor):
        return statement
    account_ids = _company_account_ids(db, actor)
    source_ids = select(CandidateSource.id).where(CandidateSource.company_id == actor.company_id, CandidateSource.platform_account_id.in_(account_ids))
    filters = {
        "boss-daily-metrics": BossDailyMetric.boss_account_id.in_(account_ids),
        "recruiters": Recruiter.id == actor.id,
        "accounts": RecruitmentAccount.recruiter_id == actor.id,
        "candidate-sources": CandidateSource.platform_account_id.in_(account_ids),
        "candidate-sync": CandidateSyncOutbox.candidate_source_id.in_(source_ids),
        "lookup-alerts": (DuplicateLookupAlert.viewer_recruiter_id == actor.id) | (DuplicateLookupAlert.matched_recruiter_id == actor.id),
        "conflicts": (Conflict.left_recruiter_id == actor.id) | (Conflict.right_recruiter_id == actor.id),
        "interviews": Interview.recruiter_id == actor.id,
        "notifications": NotificationOutbox.recipient_recruiter_id == actor.id,
        "devices": PluginDevice.recruiter_id == actor.id,
        "plugin-diagnostics": PluginDiagnostic.recruiter_id == actor.id,
        "binding-attempts": FeishuBindingAttempt.recruiter_id == actor.id,
        "scan-checkpoints": ConversationScanCheckpoint.account_display_name.in_(select(RecruitmentAccount.account_display_name).where(RecruitmentAccount.recruiter_id == actor.id)),
        "audit-logs": AuditLog.actor_id == actor.id,
    }
    return statement.where(filters[resource]) if resource in filters else statement


def _row_visible_to_actor(resource: str, row: Any, actor: Actor, db: Session) -> bool:
    if _has_company_data_scope(db, actor):
        return True
    if resource == "recruiters":
        return row.id == actor.id
    if resource == "accounts" or resource == "devices" or resource == "plugin-diagnostics":
        return getattr(row, "recruiter_id", None) == actor.id
    if resource == "candidate-sources":
        return row.platform_account_id in set(db.scalars(_company_account_ids(db, actor)).all())
    if resource == "candidate-sync":
        source = db.get(CandidateSource, row.candidate_source_id)
        return bool(source and _row_visible_to_actor("candidate-sources", source, actor, db))
    if resource == "notifications":
        return row.recipient_recruiter_id == actor.id
    if resource == "interviews":
        return row.recruiter_id == actor.id
    return True


def _bitable_client(db: Session, company_id: str):
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    config = _active_bitable(db, company_id)
    settings = get_settings()
    return BitableSyncClient(settings, app_token=config.app_token if config else None, candidate_table_id=config.candidate_table_id if config else None)


@router.get("/admin/feishu-tables")
def list_feishu_tables(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_capability(db, actor, "can_manage_feishu")
    rows = db.scalars(
        select(FeishuBitableConfig)
        .where(FeishuBitableConfig.company_id == actor.company_id)
        .order_by(FeishuBitableConfig.status.asc(), FeishuBitableConfig.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "table_name": row.table_name,
            "table_url": row.table_url,
            "app_token": f"{row.app_token[:4]}…{row.app_token[-4:]}",
            "candidate_table_id": f"{row.candidate_table_id[:4]}…{row.candidate_table_id[-4:]}",
            "status": row.status,
            "validation": row.validation_json,
            "validated_at": row.validated_at,
            "activated_at": row.activated_at,
        }
        for row in rows
    ]


@router.get("/admin/access-profiles")
def list_access_profiles(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_capability(db, actor, "can_manage_team")
    rows = db.scalars(select(RecruiterAccessProfile).where(RecruiterAccessProfile.company_id == actor.company_id)).all()
    recruiters = {row.id: row for row in db.scalars(select(Recruiter).where(Recruiter.company_id == actor.company_id)).all()}
    return [
        {
            "id": row.id,
            "recruiter_id": row.recruiter_id,
            "recruiter_name": (
                recruiters[row.recruiter_id].feishu_display_name or recruiters[row.recruiter_id].display_name
                if row.recruiter_id in recruiters
                else row.recruiter_id
            ),
            "role": recruiters[row.recruiter_id].role if row.recruiter_id in recruiters else "RECRUITER",
            "data_scope": row.data_scope,
            "can_manage_team": row.can_manage_team,
            "can_manage_feishu": row.can_manage_feishu,
            "can_manage_jobs": row.can_manage_jobs,
            "can_reset_system": row.can_reset_system,
        }
        for row in rows
    ]


@router.patch("/admin/access-profiles/{recruiter_id}")
def update_access_profile(recruiter_id: str, body: dict[str, Any], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_team")
    recruiter = db.get(Recruiter, recruiter_id)
    if not recruiter or recruiter.company_id != actor.company_id:
        raise ApplicationError("RECRUITER_NOT_FOUND", "招聘者不存在", 404)
    profile = db.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.recruiter_id == recruiter_id))
    if not profile:
        profile = RecruiterAccessProfile(company_id=actor.company_id, recruiter_id=recruiter_id)
        db.add(profile)
    allowed = {"data_scope", "can_manage_team", "can_manage_feishu", "can_manage_jobs", "can_reset_system"}
    unknown = set(body) - allowed
    if unknown:
        raise ApplicationError("FIELD_NOT_EDITABLE", f"字段不可编辑：{', '.join(sorted(unknown))}", 400)
    if body.get("data_scope") not in {None, "OWN", "COMPANY"}:
        raise ApplicationError("INVALID_DATA_SCOPE", "数据范围必须是 OWN 或 COMPANY", 400)
    capability_names = ("can_manage_team", "can_manage_feishu", "can_manage_jobs", "can_reset_system")
    if any(key in body and not isinstance(body[key], bool) for key in capability_names):
        raise ApplicationError("INVALID_CAPABILITY", "权限开关必须是布尔值", 400)
    proposed = {
        "data_scope": body.get("data_scope", profile.data_scope),
        **{key: body.get(key, getattr(profile, key)) for key in capability_names},
    }
    full_admin = proposed["data_scope"] == "COMPANY" and all(proposed[key] for key in capability_names)
    regular_recruiter = proposed["data_scope"] == "OWN" and not any(proposed[key] for key in capability_names)
    if not (full_admin or regular_recruiter):
        raise ApplicationError(
            "PARTIAL_ADMIN_PROFILE_NOT_ALLOWED",
            "权限必须选择完整管理员或普通招聘人员，不能保存半管理员状态",
            400,
        )
    if recruiter_id == actor.id and regular_recruiter:
        raise ApplicationError("ADMIN_SELF_REVOKE_FORBIDDEN", "不能移除自己的管理员权限，请让其他管理员操作", 403)
    for key, value in proposed.items():
        setattr(profile, key, value)
    recruiter.role = "ADMIN" if full_admin else "RECRUITER"
    db.add(
        AuditLog(
            company_id=actor.company_id,
            actor_id=actor.id,
            action="ADMIN_GRANTED" if full_admin else "ADMIN_REVOKED",
            entity_type="recruiter",
            entity_id=recruiter.id,
            after_json={"role": recruiter.role, **proposed},
        )
    )
    db.commit()
    return {"recruiter_id": recruiter_id, "role": recruiter.role, "data_scope": profile.data_scope, "can_manage_team": profile.can_manage_team, "can_manage_feishu": profile.can_manage_feishu, "can_manage_jobs": profile.can_manage_jobs, "can_reset_system": profile.can_reset_system}


def _feishu_identity_recruiter(db: Session, company_id: str, open_id: str, user_id: str | None, display_name: str) -> Recruiter:
    """Find the recruiter identity for a Feishu member, creating a placeholder
    when the member has not logged in yet, so administrators can be granted
    to any person in the app's contact scope before their first visit."""
    recruiter = db.scalar(select(Recruiter).where(Recruiter.company_id == company_id, Recruiter.feishu_open_id == open_id, Recruiter.status == "ACTIVE"))
    if recruiter:
        recruiter.feishu_user_id = user_id or recruiter.feishu_user_id
        recruiter.feishu_display_name = display_name or recruiter.feishu_display_name
        return recruiter
    identity_key = hashlib.sha256(f"{company_id}|{open_id}".encode()).hexdigest()
    recruiter = Recruiter(
        company_id=company_id,
        display_name=display_name.strip() or "飞书成员",
        feishu_open_id=open_id,
        feishu_user_id=user_id,
        feishu_display_name=display_name.strip() or "飞书成员",
        email=f"feishu-{identity_key[:24]}@identity.invalid",
        password_hash="FEISHU_IDENTITY_CANNOT_PASSWORD_LOGIN",
        role="RECRUITER",
        status="ACTIVE",
    )
    db.add(recruiter)
    db.flush()
    return recruiter


def _admin_profile(db: Session, company_id: str, recruiter_id: str) -> RecruiterAccessProfile:
    profile = db.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.company_id == company_id, RecruiterAccessProfile.recruiter_id == recruiter_id))
    if not profile:
        profile = RecruiterAccessProfile(company_id=company_id, recruiter_id=recruiter_id)
        db.add(profile)
    return profile


@router.put("/admin/admins")
def grant_admin(body: AdminGrantRequest, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Promote any Feishu member in the app scope to a full administrator.

    Administrators are not hard-coded: any number of members can hold admin
    capabilities, and the grant works even before the member first logs in.
    """
    _require_capability(db, actor, "can_manage_team")
    target = _feishu_identity_recruiter(db, actor.company_id, body.feishu_open_id, body.feishu_user_id, body.feishu_display_name)
    profile = _admin_profile(db, actor.company_id, target.id)
    profile.data_scope = "COMPANY"
    profile.can_manage_team = True
    profile.can_manage_feishu = True
    profile.can_manage_jobs = True
    profile.can_reset_system = True
    if target.role != "ADMIN":
        target.role = "ADMIN"
    db.add(AuditLog(company_id=actor.company_id, actor_id=actor.id, action="ADMIN_GRANTED", entity_type="recruiter", entity_id=target.id, after_json={"feishu_open_id": target.feishu_open_id, "feishu_display_name": target.feishu_display_name}))
    db.commit()
    return {"recruiter_id": target.id, "feishu_display_name": target.feishu_display_name, "data_scope": profile.data_scope, "can_manage_team": True, "can_manage_feishu": True, "can_manage_jobs": True, "can_reset_system": True}


@router.delete("/admin/admins/{recruiter_id}")
def revoke_admin(recruiter_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    """Remove administration rights from a member; the member keeps their
    Feishu identity and any BOSS account assignment."""
    _require_capability(db, actor, "can_manage_team")
    if recruiter_id == actor.id:
        raise ApplicationError("ADMIN_SELF_REVOKE_FORBIDDEN", "不能在这里移除自己的管理员权限，请让其他管理员操作", 403)
    target = db.get(Recruiter, recruiter_id)
    if not target or target.company_id != actor.company_id:
        raise ApplicationError("RECRUITER_NOT_FOUND", "招聘者不存在", 404)
    profile = db.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.company_id == actor.company_id, RecruiterAccessProfile.recruiter_id == recruiter_id))
    if profile:
        profile.data_scope = "OWN"
        profile.can_manage_team = False
        profile.can_manage_feishu = False
        profile.can_manage_jobs = False
        profile.can_reset_system = False
    if target.role == "ADMIN":
        target.role = "RECRUITER"
    db.add(AuditLog(company_id=actor.company_id, actor_id=actor.id, action="ADMIN_REVOKED", entity_type="recruiter", entity_id=target.id, after_json={"feishu_open_id": target.feishu_open_id, "feishu_display_name": target.feishu_display_name}))
    db.commit()
    return {"success": True}


@router.post("/admin/feishu-tables/validate")
def validate_feishu_table(body: FeishuTableValidateRequest, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_feishu")
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    try:
        app_token, table_id = BitableSyncClient.parse_table_url(body.table_url)
    except ValueError as exc:
        raise ApplicationError("FEISHU_TABLE_URL_INVALID", "请粘贴包含 Base 和 table 参数的飞书多维表格链接", 400) from exc
    active = _active_bitable(db, actor.company_id)
    settings = get_settings()
    if active and (active.app_token, active.candidate_table_id) == (app_token, table_id):
        raise ApplicationError("FEISHU_TABLE_ALREADY_ACTIVE", "不能再次验证当前正在使用的表格", 409)
    if not active and (settings.feishu_bitable_app_token, settings.feishu_bitable_candidate_table_id) == (app_token, table_id):
        raise ApplicationError("FEISHU_TABLE_ALREADY_ACTIVE", "不能再次验证当前正在使用的表格", 409)
    client = BitableSyncClient(settings, app_token=app_token, candidate_table_id=table_id)
    try:
        validation = client.table_probe()
        if validation["type_mismatches"]:
            raise ApplicationError("FEISHU_TABLE_FIELD_TYPE_MISMATCH", "目标表存在必需字段类型冲突，请修正后重试", 400)
        client.write_probe()
    except ApplicationError:
        raise
    except Exception as exc:
        raise ApplicationError("FEISHU_TABLE_VALIDATION_FAILED", "目标表权限或结构验证失败，请检查飞书应用权限", 400) from exc
    row = FeishuBitableConfig(
        company_id=actor.company_id,
        table_url=body.table_url.strip(),
        app_token=app_token,
        candidate_table_id=table_id,
        table_name=validation["table_name"],
        status="PENDING",
        validation_json=validation,
        validated_at=now(),
        created_by=actor.id,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "table_name": row.table_name, "record_count": validation["record_count"], "missing_fields": validation["missing_fields"], "type_mismatches": []}


@router.post("/admin/feishu-tables/{table_id}/clear-and-activate")
def activate_feishu_table(table_id: str, body: FeishuTableActivateRequest, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_feishu")
    row = db.scalar(select(FeishuBitableConfig).where(FeishuBitableConfig.id == table_id, FeishuBitableConfig.company_id == actor.company_id, FeishuBitableConfig.status == "PENDING"))
    if not row:
        raise ApplicationError("FEISHU_TABLE_NOT_FOUND", "待启用的飞书表验证记录不存在或已处理", 404)
    if body.confirmation.strip() != f"清空 {row.table_name}":
        raise ApplicationError("FEISHU_TABLE_CONFIRMATION_REQUIRED", f"请输入：清空 {row.table_name}", 400)
    from recruitment_collab.infrastructure.bitable import BitableSyncClient

    client = BitableSyncClient(get_settings(), app_token=row.app_token, candidate_table_id=row.candidate_table_id)
    try:
        deleted = client.clear_records()
        validation = client.table_probe()
        if validation["record_count"] != 0:
            raise RuntimeError("FEISHU_TABLE_NOT_EMPTY_AFTER_CLEAR")
    except Exception as exc:
        raise ApplicationError("FEISHU_TABLE_CLEAR_FAILED", "清空目标表失败，当前使用中的表格未改变", 409) from exc
    old = _active_bitable(db, actor.company_id)
    if old:
        old.status = "ARCHIVED"
    row.status, row.activated_at, row.validation_json = "ACTIVE", now(), {**row.validation_json, "record_count": 0, "cleared_records": deleted}
    db.commit()
    return {"id": row.id, "status": row.status, "table_name": row.table_name, "cleared_records": deleted}


@router.get("/admin/feishu-tables/active/open-url")
def active_feishu_table_url(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    _require_capability(db, actor, "can_manage_feishu")
    row = _active_bitable(db, actor.company_id)
    if not row or not row.table_url:
        raise ApplicationError("FEISHU_TABLE_NOT_CONFIGURED", "尚未配置飞书候选人表", 404)
    return {"table_url": row.table_url}


def _admin_member_ids(db: Session, company_id: str) -> set[str]:
    """Return full administrators; partial historical profiles are never
    preserved as administrators during a system reset."""
    ids: set[str] = set(
        db.scalars(
            select(RecruiterAccessProfile.recruiter_id).where(
                RecruiterAccessProfile.company_id == company_id,
                RecruiterAccessProfile.data_scope == "COMPANY",
                RecruiterAccessProfile.can_manage_team.is_(True),
                RecruiterAccessProfile.can_manage_feishu.is_(True),
                RecruiterAccessProfile.can_manage_jobs.is_(True),
                RecruiterAccessProfile.can_reset_system.is_(True),
            )
        ).all()
    )
    ids |= set(db.scalars(select(Recruiter.id).where(Recruiter.company_id == company_id, Recruiter.role == "ADMIN", Recruiter.status == "ACTIVE")).all())
    return ids


def _reset_preview(db: Session, company_id: str, actor_id: str) -> tuple[dict[str, int], str, list[Recruiter]]:
    keep_ids = _admin_member_ids(db, company_id)
    keep_ids.add(actor_id)  # The requesting administrator always survives.
    keep = list(db.scalars(select(Recruiter).where(Recruiter.company_id == company_id, Recruiter.id.in_(keep_ids), Recruiter.status == "ACTIVE")).all())
    keep_ids = {recruiter.id for recruiter in keep}
    models: list[tuple[str, Any]] = [
        ("binding_attempts", FeishuBindingAttempt), ("accounts", RecruitmentAccount), ("boss_assignments", BossAccountAssignment),
        ("devices", PluginDevice), ("candidate_sources", CandidateSource), ("engagements", Engagement),
        ("interviews", Interview), ("events", RecruitmentEvent), ("conflicts", Conflict),
        ("conflict_exclusions", ConflictExclusion), ("candidate_sync", CandidateSyncOutbox), ("notifications", Notification),
        ("notification_outbox", NotificationOutbox), ("lookup_alerts", DuplicateLookupAlert),
        ("checkpoints", ConversationScanCheckpoint), ("diagnostics", PluginDiagnostic), ("unmapped_jobs", UnmappedJob),
        ("audit_logs", AuditLog), ("legacy_recruiters", Recruiter),
    ]
    counts: dict[str, int] = {}
    for name, model in models:
        if model is Recruiter:
            statement = select(func.count()).select_from(model).where(model.company_id == company_id, model.id.not_in(keep_ids))
        elif hasattr(model, "company_id"):
            statement = select(func.count()).select_from(model).where(model.company_id == company_id)
        else:
            statement = select(func.count()).select_from(model).where(MockFeishuMessage.recipient_recruiter_id.in_(select(Recruiter.id).where(Recruiter.company_id == company_id)))
        counts[name] = int(db.scalar(statement) or 0)
    version = hashlib.sha256(f"{company_id}|{','.join(sorted(keep_ids))}|{sorted(counts.items())}".encode()).hexdigest()
    return counts, version, keep


@router.get("/admin/system-reset/preview")
def system_reset_preview(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_reset_system")
    counts, version, keep = _reset_preview(db, actor.company_id, actor.id)
    active_table = _active_bitable(db, actor.company_id)
    return {
        "preview_version": version,
        "counts": counts,
        "keep_identities": [{"id": recruiter.id, "display_name": recruiter.display_name, "feishu_display_name": recruiter.feishu_display_name} for recruiter in keep],
        "active_table": active_table.table_name if active_table else None,
        "irreversible": True,
    }


@router.post("/admin/system-reset")
def system_reset(body: SystemResetRequest, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_reset_system")
    counts, version, keep = _reset_preview(db, actor.company_id, actor.id)
    keep_ids = [recruiter.id for recruiter in keep]
    if body.preview_version != version or body.confirmation.strip() != "重新初始化同步":
        raise ApplicationError("RESET_CONFIRMATION_REQUIRED", "预览已变化或确认词不正确，请重新预览并输入“重新初始化同步”", 409)
    setting = _company_settings(db, actor.company_id)
    if setting.reset_in_progress:
        raise ApplicationError("RESET_ALREADY_RUNNING", "系统重置正在执行", 409)
    setting.reset_in_progress = True
    db.commit()
    try:
        company_id = actor.company_id
        db.execute(delete(Notification).where(Notification.company_id == company_id))
        db.execute(delete(NotificationOutbox).where(NotificationOutbox.company_id == company_id))
        models_to_delete: tuple[Any, ...] = (DuplicateLookupAlert, ConflictExclusion, Conflict, CandidateSyncOutbox, Interview, RecruitmentEvent, Engagement, CandidateSource, ConversationScanCheckpoint, PluginDiagnostic, FeishuBindingAttempt, UnmappedJob, MockFeishuMessage)
        for model in models_to_delete:
            if model is MockFeishuMessage:
                db.execute(delete(model).where(model.recipient_recruiter_id.in_(select(Recruiter.id).where(Recruiter.company_id == company_id))))
            else:
                db.execute(delete(model).where(model.company_id == company_id))
        db.execute(delete(BossAccountAssignment).where(BossAccountAssignment.company_id == company_id))
        db.execute(delete(RecruitmentAccount).where(RecruitmentAccount.company_id == company_id))
        db.execute(delete(PluginDevice).where(PluginDevice.company_id == company_id))
        db.execute(delete(RecruiterAccessProfile).where(RecruiterAccessProfile.company_id == company_id, RecruiterAccessProfile.recruiter_id.not_in(keep_ids)))
        db.execute(update(FeishuBitableConfig).where(FeishuBitableConfig.company_id == company_id, FeishuBitableConfig.created_by.not_in(keep_ids)).values(created_by=None))
        db.execute(delete(AuditLog).where(AuditLog.company_id == company_id))
        # A previous reset job may reference a recruiter removed by this run.
        db.execute(update(SystemResetJob).where(SystemResetJob.company_id == company_id).values(requested_by=actor.id))
        db.execute(delete(Recruiter).where(Recruiter.company_id == company_id, Recruiter.id.not_in(keep_ids)))
        db.execute(update(WorkerHeartbeat).values(total_processed=0, last_error_code=None, last_success_at=None, status="HEALTHY"))
        setting.reset_generation += 1
        setting.reset_in_progress = False
        job = db.scalar(select(SystemResetJob).where(SystemResetJob.company_id == company_id))
        if not job:
            job = SystemResetJob(company_id=company_id, requested_by=actor.id, preview_version=version)
            db.add(job)
        else:
            # The previous requester may have been deleted by this reset.
            job.requested_by = actor.id
        job.status, job.preview_version, job.counts_json, job.finished_at, job.error_message = "COMPLETED", version, counts, now(), None
        db.flush()
        db.add(AuditLog(company_id=company_id, actor_id=actor.id, action="SYSTEM_RESET_COMPLETED", entity_type="SystemResetJob", entity_id=job.id, after_json={"counts": counts}))
        db.commit()
    except Exception as exc:
        db.rollback()
        setting = _company_settings(db, actor.company_id)
        setting.reset_in_progress = False
        db.commit()
        raise ApplicationError("SYSTEM_RESET_FAILED", "系统重置失败，未完成清理，请检查服务日志", 500) from exc
    return {"status": "COMPLETED", "counts": counts, "kept_recruiter_ids": keep_ids, "reset_generation": setting.reset_generation}


@router.get("/admin/extension-release")
def extension_release(response: Response, actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    package, metadata = _extension_release()
    return {**metadata, "file_name": metadata.get("file_name", package.name), "size_bytes": package.stat().st_size}


@router.get("/admin/extension-release/download")
def extension_release_download(actor: Actor = Depends(require_admin)) -> FileResponse:
    package, metadata = _extension_release()
    return FileResponse(
        package,
        media_type="application/zip",
        filename=metadata.get("file_name", package.name),
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.post("/admin/devices/{device_id}/revoke")
def admin_revoke_device(device_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    # The console is now open to every Feishu member in the app scope, so
    # company-wide mutations must be capability-gated instead of relying on
    # the previous admin-only login.
    _require_capability(db, actor, "can_manage_team")
    device = db.scalar(select(PluginDevice).where(PluginDevice.device_id == device_id, PluginDevice.company_id == actor.company_id))
    if not device:
        raise ApplicationError("DEVICE_NOT_FOUND", "扩展设备不存在", 404)
    device.status, device.revoked_at, device.refresh_token_hash = "REVOKED", now(), None
    db.commit()
    return {"success": True}


@router.get("/admin/boss-accounts")
def list_boss_accounts(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_capability(db, actor, "can_manage_team")
    accounts = db.scalars(select(RecruitmentAccount).where(RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.status == "ACTIVE").order_by(RecruitmentAccount.account_display_name)).all()
    result = []
    for account in accounts:
        assignment = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE"))
        source_count = int(db.scalar(select(func.count()).select_from(CandidateSource).where(CandidateSource.platform_account_id == account.id)) or 0)
        device_count = int(db.scalar(select(func.count()).select_from(PluginDevice).where(PluginDevice.recruiter_id == assignment.feishu_recruiter_id, PluginDevice.status == "ACTIVE")) or 0) if assignment and assignment.feishu_recruiter_id else 0
        result.append({
            "id": account.id, "boss_account": account.account_display_name, "platform": account.platform,
            "assignment_version": assignment.assignment_version if assignment else 0,
            "feishu_open_id": assignment.feishu_open_id if assignment else None,
            "feishu_display_name": assignment.feishu_display_name if assignment else None,
            "source_count": source_count, "active_device_count": device_count,
            "assigned_at": assignment.assigned_at if assignment else None,
            "status": "ASSIGNED" if assignment and assignment.feishu_recruiter_id else "UNASSIGNED",
        })
    return result


@router.get("/admin/feishu/directory")
async def feishu_directory(query: str = Query(default="", max_length=100), actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_capability(db, actor, "can_manage_team")
    settings = get_settings()
    # Mock/development mode intentionally uses the local identity cache.  In
    # real mode the response must be the current Feishu app-visible scope;
    # returning previously logged-in identities on provider failure made the
    # UI look complete while silently hiding most of the directory.
    if settings.feishu_mode != "real":
        cached = db.scalars(select(Recruiter).where(Recruiter.company_id == actor.company_id, Recruiter.feishu_open_id.is_not(None), Recruiter.status == "ACTIVE")).all()
        return [
            {"open_id": r.feishu_open_id, "user_id": r.feishu_user_id, "display_name": r.feishu_display_name or r.display_name}
            for r in cached
            if not query or query.casefold() in (r.feishu_display_name or r.display_name).casefold()
        ]
    from recruitment_collab.infrastructure.feishu import FeishuDirectoryClient, FeishuDirectoryError
    try:
        return await FeishuDirectoryClient(settings).list_users(query)
    except FeishuDirectoryError as exc:
        if exc.stage == "USER_FIELDS" and exc.code == "NAME_PERMISSION_MISSING":
            raise ApplicationError(
                "FEISHU_DIRECTORY_NAME_PERMISSION_REQUIRED",
                "飞书应用缺少“获取用户基本信息”权限，请在开放平台开通 contact:user.base:readonly 并发布新版本",
                503,
            ) from exc
        raise ApplicationError("FEISHU_DIRECTORY_UNAVAILABLE", "飞书通讯录暂时不可用，请检查应用通讯录权限和已发布版本后重试", 503) from exc
    except Exception as exc:
        raise ApplicationError("FEISHU_DIRECTORY_UNAVAILABLE", "飞书通讯录暂时不可用，请检查应用通讯录权限和已发布版本后重试", 503) from exc


@router.put("/admin/boss-accounts/{account_id}/assignment")
def replace_boss_assignment(account_id: str, body: BossAccountAssignmentRequest, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_team")
    account = db.scalar(select(RecruitmentAccount).where(RecruitmentAccount.id == account_id, RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.status == "ACTIVE"))
    if not account:
        raise ApplicationError("BOSS_ACCOUNT_NOT_FOUND", "BOSS 账号不存在", 404)
    current = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.boss_account_id == account.id, BossAccountAssignment.status == "ACTIVE"))
    old_owner_id = current.feishu_recruiter_id if current else account.recruiter_id
    if current and body.expected_version is not None and current.assignment_version != body.expected_version:
        raise ApplicationError("ASSIGNMENT_VERSION_CONFLICT", "账号绑定已被其他管理员更新，请刷新后重试", 409)
    target = db.scalar(select(Recruiter).where(Recruiter.company_id == actor.company_id, Recruiter.feishu_open_id == body.feishu_open_id, Recruiter.status == "ACTIVE"))
    if not target:
        identity_key = hashlib.sha256(f"{actor.company_id}|{body.feishu_open_id}".encode()).hexdigest()
        target = Recruiter(company_id=actor.company_id, display_name=body.feishu_display_name, feishu_open_id=body.feishu_open_id, feishu_user_id=body.feishu_user_id, feishu_display_name=body.feishu_display_name, email=f"feishu-{identity_key[:24]}@identity.invalid", password_hash="FEISHU_IDENTITY_CANNOT_PASSWORD_LOGIN", role="RECRUITER", status="ACTIVE")
        db.add(target); db.flush(); db.add(RecruiterAccessProfile(company_id=actor.company_id, recruiter_id=target.id, data_scope="OWN"))
    occupied = db.scalar(select(BossAccountAssignment).where(BossAccountAssignment.company_id == actor.company_id, BossAccountAssignment.feishu_recruiter_id == target.id, BossAccountAssignment.status == "ACTIVE", BossAccountAssignment.boss_account_id != account.id))
    if occupied:
        raise ApplicationError("FEISHU_ALREADY_ASSIGNED", "该飞书账号已经绑定其他 BOSS 账号，请先替换或解绑", 409)
    if current and current.feishu_recruiter_id == target.id:
        return {"id": account.id, "status": "ASSIGNED", "feishu_display_name": target.feishu_display_name, "assignment_version": current.assignment_version}
    stamp = now()
    if current:
        current.status, current.unassigned_at = "REPLACED", stamp
    assignment = BossAccountAssignment(company_id=actor.company_id, boss_account_id=account.id, feishu_recruiter_id=target.id, feishu_open_id=target.feishu_open_id, feishu_display_name=target.feishu_display_name, assignment_version=(current.assignment_version + 1 if current else 1), assigned_at=stamp)
    db.add(assignment); db.flush()
    if current: current.replaced_by_id = assignment.id
    account.recruiter_id = target.id
    for device in db.scalars(select(PluginDevice).where(PluginDevice.company_id == actor.company_id, PluginDevice.recruiter_id == old_owner_id, PluginDevice.status == "ACTIVE")).all():
        device.status, device.revoked_at, device.refresh_token_hash = "REVOKED", stamp, None
    db.add(AuditLog(company_id=actor.company_id, actor_id=actor.id, action="BOSS_ACCOUNT_ASSIGNMENT_REPLACED", entity_type="RecruitmentAccount", entity_id=account.id, after_json={"feishu_open_id": target.feishu_open_id, "feishu_display_name": target.feishu_display_name}))
    inherited_count = RecruitmentCollaborationService(db).requeue_account_sources(account.id)
    db.commit()
    return {"id": account.id, "status": "ASSIGNED", "feishu_display_name": target.feishu_display_name, "assignment_version": assignment.assignment_version, "inherited_candidate_count": inherited_count}


@router.post("/admin/candidate-sync/{outbox_id}/retry")
def admin_retry_candidate_sync(outbox_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.id == outbox_id, CandidateSyncOutbox.company_id == actor.company_id))
    # Personal-scope members may only retry their own accounts' sync rows.
    if not row or not _row_visible_to_actor("candidate-sync", row, actor, db) or row.status not in {"FAILED", "PENDING"}:
        raise ApplicationError("SYNC_RETRY_NOT_ALLOWED", "同步任务不存在或当前状态不允许重试", 409)
    if not row.payload_json:
        raise ApplicationError("SYNC_PAYLOAD_EXPIRED", "同步载荷已按保留期限清除，请由扩展重新同步该候选人", 409)
    row.status, row.retry_count, row.next_retry_at, row.last_error = "PENDING", 0, now(), None
    db.commit()
    return {"success": True}


@router.get("/admin/settings", include_in_schema=False)
def settings_early(actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Keep the concrete settings endpoint ahead of the generic resource route."""
    row = serialize(_company_settings(db, actor.company_id))
    config = get_settings()
    row.update({"api_base_url": f"{config.public_web_url.rstrip('/')}/api/v1", "web_url": config.public_web_url, "environment": config.app_env})
    return row


@router.patch("/admin/settings", include_in_schema=False)
def update_settings_early(body: RecruitmentSettingsUpdate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_team")
    return _update_settings(body, actor, db)


@router.get("/admin/{resource}")
def admin_list(
    resource: str,
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="oldest", pattern="^(oldest|latest)$"),
    actor: Actor = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    model = ADMIN_RESOURCES.get(resource)
    if not model:
        raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在", 404)
    statement = select(model)
    if hasattr(model, "company_id"):
        statement = statement.where(model.company_id == actor.company_id)
    statement = _scope_statement(resource, statement, actor, db)
    if hasattr(model, "created_at"):
        statement = statement.order_by((model.created_at.desc() if sort == "latest" else model.created_at.asc()), model.id.desc() if sort == "latest" else model.id.asc())
    elif hasattr(model, "id"):
        statement = statement.order_by(model.id.asc())
    rows = db.scalars(statement.offset(offset).limit(limit)).all()
    serialized = [admin_serialize(row) for row in rows]
    if resource == "candidate-sources":
        counts = _candidate_message_counts(db, [row.id for row in rows])
        for row, data in zip(rows, serialized):
            count, last_message_at = counts.get(row.id, (0, None))
            data["message_sent_count"] = count
            data["last_message_sent_at"] = last_message_at
    return serialized


def _admin_row(resource: str, row_id: str, actor: Actor, db: Session):
    model = ADMIN_RESOURCES.get(resource)
    if not model:
        raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在", 404)
    row = db.get(model, row_id)
    if not row or hasattr(row, "company_id") and row.company_id != actor.company_id or not _row_visible_to_actor(resource, row, actor, db):
        raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在", 404)
    return row


@router.patch("/admin/unmapped-jobs/{unmapped_id}")
def map_unmapped_job(unmapped_id: str, body: dict[str, str], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_jobs")
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


@router.patch("/admin/{resource}/{row_id}")
def admin_update(resource: str, row_id: str, body: dict[str, Any], actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _admin_row(resource, row_id, actor, db)
    if resource in {"recruiters", "accounts", "devices"}:
        _require_capability(db, actor, "can_manage_team")
    if resource in {"jobs", "job-aliases", "unmapped-jobs"}:
        _require_capability(db, actor, "can_manage_jobs")
    allowed = ADMIN_EDITABLE_FIELDS.get(resource, set())
    unknown = set(body) - allowed
    if unknown:
        raise ApplicationError("FIELD_NOT_EDITABLE", f"字段不可编辑：{', '.join(sorted(unknown))}", 400)
    if resource == "recruiters" and "role" in body and body["role"] not in {"ADMIN", "HR_MANAGER", "RECRUITER"}:
        raise ApplicationError("INVALID_ROLE", "角色无效", 400)
    if "status" in body and body["status"] not in {"ACTIVE", "DISABLED", "REVOKED", "ARCHIVED"}:
        raise ApplicationError("INVALID_STATUS", "状态无效", 400)
    for key, value in body.items():
        if key == "password":
            if value:
                row.password_hash = hash_password(str(value))
        elif hasattr(row, key):
            setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApplicationError("RESOURCE_CONFLICT", "保存失败：数据与现有记录冲突", 409) from exc
    return admin_serialize(row)


@router.delete("/admin/{resource}/{row_id}")
def admin_delete(resource: str, row_id: str, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, bool]:
    if resource not in ADMIN_DELETABLE_RESOURCES:
        raise ApplicationError("RESOURCE_DELETE_NOT_ALLOWED", "该类业务记录不能通过通用接口删除", 405)
    row = _admin_row(resource, row_id, actor, db)
    if resource in {"recruiters", "accounts", "devices"}:
        _require_capability(db, actor, "can_manage_team")
    if resource in {"jobs", "job-aliases", "unmapped-jobs"}:
        _require_capability(db, actor, "can_manage_jobs")
    db.delete(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApplicationError("RESOURCE_IN_USE", "该记录仍被业务数据引用，无法直接删除；请先处理关联记录", 409) from exc
    return {"success": True}


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
    if not row or row.company_id != actor.company_id or not _row_visible_to_actor("candidate-sources", row, actor, db):
        raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
    data = serialize(row)
    message_count, last_message_at = _candidate_message_counts(db, [source_id]).get(source_id, (0, None))
    data["message_sent_count"] = message_count
    data["last_message_sent_at"] = last_message_at
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


@router.post("/admin/jobs")
def create_job(body: JobCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_jobs")
    row = RecruitmentJob(company_id=actor.company_id, **body.model_dump())
    db.add(row)
    db.commit()
    return serialize(row)


@router.post("/admin/job-aliases")
def create_alias(body: AliasCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_jobs")
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
    _require_capability(db, actor, "can_manage_team")
    if body.role not in {"ADMIN", "HR_MANAGER", "RECRUITER"}:
        raise ApplicationError("INVALID_ROLE", "角色无效")
    row = Recruiter(company_id=actor.company_id, display_name=body.display_name, email=body.email, role=body.role, password_hash=hash_password(body.password))
    db.add(row)
    db.flush()
    db.add(RecruiterAccessProfile(
        company_id=actor.company_id,
        recruiter_id=row.id,
        data_scope="COMPANY" if body.role == "ADMIN" else "OWN",
        can_manage_team=body.role == "ADMIN",
        can_manage_feishu=body.role == "ADMIN",
        can_manage_jobs=body.role == "ADMIN",
        can_reset_system=body.role == "ADMIN",
    ))
    db.commit()
    data = serialize(row)
    data.pop("password_hash")
    return data


@router.post("/admin/accounts")
def create_account(body: AccountCreate, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_capability(db, actor, "can_manage_team")
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


def _update_settings(body: RecruitmentSettingsUpdate, actor: Actor, db: Session) -> dict[str, Any]:
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
