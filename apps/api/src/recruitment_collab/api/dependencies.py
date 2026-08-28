from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from recruitment_collab.application.collaboration import ApplicationError
from recruitment_collab.infrastructure.database import get_db
from recruitment_collab.infrastructure.models import Recruiter
from recruitment_collab.infrastructure.security import decode_token


@dataclass(frozen=True)
class Actor:
    id: str
    company_id: str
    role: str
    display_name: str


def current_actor(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Actor:
    if not authorization.startswith("Bearer "):
        raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
    try:
        payload = decode_token(authorization[7:])
    except jwt.PyJWTError as exc:
        raise ApplicationError("INVALID_TOKEN", "登录已失效", 401) from exc
    recruiter = db.get(Recruiter, payload["sub"])
    if not recruiter or recruiter.status != "ACTIVE":
        raise ApplicationError("ACCOUNT_DISABLED", "账号不存在或已停用", 401)
    return Actor(recruiter.id, recruiter.company_id, recruiter.role, recruiter.display_name)


def require_admin(actor: Actor = Depends(current_actor)) -> Actor:
    if actor.role not in {"ADMIN", "HR_MANAGER"}:
        raise ApplicationError("FORBIDDEN", "当前账号无管理权限", 403)
    return actor

