from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from recruitment_collab.application.collaboration import ApplicationError
from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.models import Company, PluginDevice, Recruiter
from recruitment_collab.infrastructure.security import decode_token


@dataclass(frozen=True)
class Actor:
    id: str
    company_id: str
    role: str
    display_name: str


ADMIN_SESSION_COOKIE = "recruitment_admin_session"
ADMIN_ROLES = {"ADMIN", "HR_MANAGER"}


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
    return _authorized_actor(authorization, db, session_cookie=request.cookies.get(ADMIN_SESSION_COOKIE, ""))


def plugin_actor(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Actor:
    if authorization.startswith("Bearer "):
        return _authorized_actor(authorization, db, require_device=True)
    if not get_settings().is_development:
        raise ApplicationError("DEVICE_AUTH_REQUIRED", "请在扩展中使用飞书登录", 401)
    company_id = bootstrap_company_id(db)
    return Actor("", company_id, "DEVELOPMENT_PLUGIN", "")


def require_admin(actor: Actor = Depends(current_actor)) -> Actor:
    if actor.role not in ADMIN_ROLES:
        raise ApplicationError("FORBIDDEN", "当前账号无管理权限", 403)
    return actor


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
