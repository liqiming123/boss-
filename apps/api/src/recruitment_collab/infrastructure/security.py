from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher

from recruitment_collab.config.settings import get_settings

hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_token(subject: str, company_id: str, role: str, kind: str = "access", device_id: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    lifetime = timedelta(minutes=settings.access_token_minutes) if kind == "access" else timedelta(days=settings.refresh_token_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "company_id": company_id,
        "role": role,
        "type": kind,
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_hex(12),
    }
    if device_id:
        payload["device_id"] = device_id
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str, expected_kind: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    if payload.get("type") != expected_kind:
        raise jwt.InvalidTokenError("token type mismatch")
    return payload
