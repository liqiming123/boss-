from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from recruitment_collab.application.collaboration import ApplicationError
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.models import BossAccountAssignment, Company, PluginDevice, Recruiter, RecruiterAccessProfile, RecruitmentAccount
from recruitment_collab.infrastructure.security import decode_token


@dataclass(frozen=True)
class Actor:
    id: str
    company_id: str
    role: str
    display_name: str


ADMIN_SESSION_COOKIE = "recruitment_admin_session"
def _authorized_actor(authorization: str, db: Session, require_device: bool = False, session_cookie: str = "") -> Actor:
    token = authorization[7:] if authorization.startswith("Bearer ") else session_cookie
    expected_kind = "access" if authorization.startswith("Bearer ") else "web_session"
    if not token:
        raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
    try:
        payload = decode_token(token, expected_kind)
    except jwt.PyJWTError as exc:
        raise ApplicationError("INVALID_TOKEN", "登录已失效", 401) from exc
    recruiter = db.get(Recruiter, payload["sub"])
    if not recruiter or recruiter.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "账号不存在或已停用", 401)
    # Web sessions are tied to the Feishu identity that created them. When a
    # binding is moved to another BOSS recruiter, the old browser cookie must
    # stop working immediately instead of continuing to show stale account
    # data until its JWT expires.
    if expected_kind == "web_session" and not recruiter.feishu_open_id:
        raise ApplicationError("STALE_WEB_SESSION", "管理后台登录身份已变更，请重新使用飞书登录", 401)
    if require_device and not get_settings().is_development:
        device_id = str(payload.get("device_id") or "")
        device = (
            db.scalar(
                select(PluginDevice).where(PluginDevice.device_id == device_id, PluginDevice.recruiter_id == recruiter.id, PluginDevice.status == "ACTIVE")
            )
            if device_id
            else None
        )
        if not device:
            raise ApplicationError("DEVICE_AUTH_REQUIRED", "请在扩展中使用飞书登录", 401)
    return Actor(recruiter.id, recruiter.company_id, recruiter.role, recruiter.display_name)


def current_actor(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Actor:
    actor = _authorized_actor(authorization, db, session_cookie=request.cookies.get(ADMIN_SESSION_COOKIE, ""))
    # Web login grants access to onboarding and downloads. Business details
    # require both a current assignment and an authorized extension device.
    if not authorization.startswith("Bearer ") and request.url.path.rstrip("/") not in {
        "/api/v1/auth/me", "/api/v1/auth/logout",
        "/api/v1/admin/extension-release", "/api/v1/admin/extension-release/download",
    }:
        state = workspace_state(db, actor)
        if state["status"] == "UNASSIGNED":
            raise ApplicationError("BOSS_ASSIGNMENT_REQUIRED", "飞书登录成功，请先联系管理员分配 BOSS 账号", 403)
        if state["status"] == "EXTENSION_REQUIRED":
            raise ApplicationError("EXTENSION_CONNECTION_REQUIRED", "BOSS 账号已分配，请先在对应 BOSS 页面使用飞书登录扩展", 403)
    return actor


def workspace_state(db: Session, actor: Actor) -> dict:
    profile = db.scalar(select(RecruiterAccessProfile).where(RecruiterAccessProfile.recruiter_id == actor.id, RecruiterAccessProfile.company_id == actor.company_id))
    # The console deliberately has two workspace modes.  A historical
    # partially-enabled profile must not become an administrator merely
    # because one capability or COMPANY was toggled.  Full capabilities are
    # accepted for compatibility with grants created before role sync was
    # introduced; new grants also set role=ADMIN.
    full_admin_profile = bool(
        profile
        and profile.data_scope == "COMPANY"
        and profile.can_manage_team
        and profile.can_manage_feishu
        and profile.can_manage_jobs
        and profile.can_reset_system
    )
    if actor.role.upper() == "ADMIN" or full_admin_profile:
        return {"status": "ADMIN", "boss_account_name": None}
    account = db.scalar(select(RecruitmentAccount).join(BossAccountAssignment, BossAccountAssignment.boss_account_id == RecruitmentAccount.id).where(
        RecruitmentAccount.company_id == actor.company_id, RecruitmentAccount.status == "ACTIVE",
        RecruitmentAccount.recruiter_id == actor.id, BossAccountAssignment.status == "ACTIVE",
        BossAccountAssignment.feishu_recruiter_id == actor.id,
    ))
    if not account:
        return {"status": "UNASSIGNED", "boss_account_name": None}
    device = db.scalar(select(PluginDevice.id).where(PluginDevice.company_id == actor.company_id, PluginDevice.recruiter_id == actor.id, PluginDevice.status == "ACTIVE").limit(1))
    return {"status": "READY" if device else "EXTENSION_REQUIRED", "boss_account_name": account.account_display_name}


def plugin_actor(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Actor:
    if authorization.startswith("Bearer "):
        return _authorized_actor(authorization, db, require_device=True)
    if not get_settings().is_development:
        raise ApplicationError("DEVICE_AUTH_REQUIRED", "请在扩展中使用飞书登录", 401)
    company_id = bootstrap_company_id(db)
    return Actor("", company_id, "DEVELOPMENT_PLUGIN", "")


def require_admin(actor: Actor = Depends(current_actor)) -> Actor:
    # The console is shared by recruiters and administrators.  Access to
    # company-wide data and mutations is enforced by RecruiterAccessProfile
    # capabilities in the API layer, rather than by a hard-coded role name.
    return actor


def has_admin_scope(actor: Actor) -> bool:
    """Return whether an actor is in the configured console scope.

    With no ADMIN_ROLES configured this deliberately matches plugin scope:
    active, company-scoped actors are allowed by ``current_actor``.
    """
    configured_roles = {role.upper() for role in get_settings().admin_roles}
    return not configured_roles or actor.role.upper() in configured_roles


def bootstrap_company_id(db: Session = Depends(get_db)) -> str:
    """Resolve the configured tenant only for the unauthenticated OAuth bootstrap."""
    code = get_settings().plugin_company_code.strip()
    if code:
        company = db.scalar(select(Company).where(Company.code == code, Company.status == "ACTIVE"))
        if not company:
            raise ApplicationError("PLUGIN_COMPANY_NOT_FOUND", "插件所属公司配置无效", 503)
        return company.id
    companies = db.scalars(select(Company).where(Company.status == "ACTIVE").limit(2)).all()
    if len(companies) != 1:
        raise ApplicationError("PLUGIN_COMPANY_REQUIRED", "多公司环境必须配置 PLUGIN_COMPANY_CODE", 503)
    return companies[0].id


def plugin_company_id(actor: Actor = Depends(plugin_actor)) -> str:
    return actor.company_id
