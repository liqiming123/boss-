from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy.orm import Session

from recruitment_collab.config.settings import Settings

from .models import MockFeishuMessage


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    status: str = "SENT"


class FeishuMessageClient(Protocol):
    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any]) -> SendResult: ...


class MockFeishuClient:
    def __init__(self, session: Session):
        self.session = session

    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any]) -> SendResult:
        row = MockFeishuMessage(recipient_recruiter_id=recipient_recruiter_id, payload_json=payload)
        self.session.add(row)
        self.session.flush()
        return SendResult(row.id)


class RealFeishuClient:
    API = "https://open.feishu.cn/open-apis"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise ValueError("真实飞书模式需要 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10))
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _tenant_token(self) -> str:
        async with self._lock:
            if self._token and self._expires_at > time.time() + 120:
                return self._token
            response = await self.client.post(
                f"{self.API}/auth/v3/tenant_access_token/internal", json={"app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu token error: code={data.get('code')}")
            self._token, self._expires_at = data["tenant_access_token"], time.time() + int(data.get("expire", 7200))
            return self._token

    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any]) -> SendResult:
        token = await self._tenant_token()
        response = await self.client.post(
            f"{self.API}/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": recipient_recruiter_id, "msg_type": "interactive", "content": __import__("json").dumps(payload, ensure_ascii=False)},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu send error: code={data.get('code')}")
        return SendResult(data["data"]["message_id"])

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass(frozen=True)
class FeishuIdentity:
    open_id: str
    user_id: str | None
    display_name: str


class FeishuOAuthClient:
    API = "https://open.feishu.cn/open-apis"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.feishu_app_id or not settings.feishu_app_secret or not settings.feishu_redirect_uri:
            raise ValueError("飞书绑定需要配置 FEISHU_APP_ID、FEISHU_APP_SECRET 和 FEISHU_REDIRECT_URI")
        self.settings = settings
        self.client = client

    def authorization_url(self, state: str) -> str:
        query = urlencode({"app_id": self.settings.feishu_app_id, "redirect_uri": self.settings.feishu_redirect_uri, "state": state})
        return f"https://accounts.feishu.cn/open-apis/authen/v1/authorize?{query}"

    @staticmethod
    def _api_data(response: httpx.Response, step: str) -> dict[str, Any]:
        response.raise_for_status()
        body = response.json()
        if body.get("code", 0) != 0:
            raise RuntimeError(f"FEISHU_OAUTH_{step}_ERROR:{body.get('code')}")
        return body.get("data", body)

    async def _exchange_code(self, client: httpx.AsyncClient, code: str) -> FeishuIdentity:
        app_token = self._api_data(
            await client.post(
                f"{self.API}/auth/v3/app_access_token/internal", json={"app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret}
            ),
            "APP_TOKEN",
        )["app_access_token"]
        token_data = self._api_data(
            await client.post(
                f"{self.API}/authen/v1/access_token",
                headers={"Authorization": f"Bearer {app_token}"},
                json={"grant_type": "authorization_code", "code": code},
            ),
            "USER_TOKEN",
        )
        user_data = self._api_data(
            await client.get(f"{self.API}/authen/v1/user_info", headers={"Authorization": f"Bearer {token_data['access_token']}"}),
            "USER_INFO",
        )
        open_id = str(user_data.get("open_id") or "").strip()
        if not open_id:
            raise RuntimeError("FEISHU_OAUTH_OPEN_ID_MISSING")
        return FeishuIdentity(
            open_id=open_id, user_id=str(user_data.get("user_id") or "").strip() or None, display_name=str(user_data.get("name") or "飞书用户").strip()
        )

    async def exchange_code(self, code: str) -> FeishuIdentity:
        if self.client:
            return await self._exchange_code(self.client, code)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            return await self._exchange_code(client, code)


class FeishuCardBuilder:
    def duplicate_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        browsing = payload.get("type") == "DUPLICATE_LOOKUP"
        title = "候选人浏览查重提醒" if browsing else "候选人跟进冲突提醒"
        context = f"**当前查看者**：{payload.get('viewer_name', '未知')}\n**岗位**：{payload.get('job_name', '未知')}\n" if browsing else ""
        elements: list[dict[str, Any]] = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**候选人**：{payload.get('candidate_name', '未知')}\n{context}**匹配依据**：{payload.get('match_reason', '未知')}\n{'当前仅浏览，尚未确认已发送消息。' if browsing else '请人工核对，不会自动合并。'}",
                },
            }
        ]
        if not browsing:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看详情"},
                            "type": "primary",
                            "value": {"action": "view", "conflict_id": payload.get("conflict_id")},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "我知道了"},
                            "value": {"action": "acknowledge", "conflict_id": payload.get("conflict_id")},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "不是同一个人"},
                            "value": {"action": "exclude", "conflict_id": payload.get("conflict_id")},
                        },
                    ],
                }
            )
        return {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": title}, "template": "orange"}, "elements": elements}


class FeishuCallbackVerifier:
    """Validates callback freshness/signature and decrypts encrypted callback envelopes."""

    def __init__(self, encrypt_key: str, verification_token: str, max_age_seconds: int = 300):
        self.encrypt_key = encrypt_key
        self.verification_token = verification_token
        self.max_age_seconds = max_age_seconds

    def verify_signature(self, timestamp: str, nonce: str, encrypted_body: str, signature: str) -> bool:
        try:
            if abs(int(time.time()) - int(timestamp)) > self.max_age_seconds:
                return False
        except ValueError:
            return False
        expected = hashlib.sha256(f"{timestamp}{nonce}{self.encrypt_key}{encrypted_body}".encode()).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_token(self, token: str) -> bool:
        return bool(self.verification_token) and hmac.compare_digest(token, self.verification_token)

    def decrypt(self, encrypted_body: str) -> dict[str, Any]:
        key = hashlib.sha256(self.encrypt_key.encode()).digest()
        encrypted = base64.b64decode(encrypted_body, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        padding = padded[-1]
        if padding < 1 or padding > 16:
            raise ValueError("invalid callback padding")
        return json.loads(padded[:-padding].decode("utf-8"))
