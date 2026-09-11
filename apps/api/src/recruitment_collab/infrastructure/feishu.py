from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode

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
    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any], idempotency_key: str | None = None) -> SendResult: ...


class MockFeishuClient:
    def __init__(self, session: Session):
        self.session = session

    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any], idempotency_key: str | None = None) -> SendResult:
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

    async def send_card(self, recipient_recruiter_id: str, payload: dict[str, Any], idempotency_key: str | None = None) -> SendResult:
        recipient_recruiter_id = str(recipient_recruiter_id or "").strip()
        if not recipient_recruiter_id:
            raise ValueError("Feishu recipient open_id is empty")
        token = await self._tenant_token()
        params = {"receive_id_type": "open_id"}
        if idempotency_key:
            # Feishu's uuid makes retries of the same outbox item safe when a
            # worker crashes after the provider accepted the request.
            params["uuid"] = idempotency_key
        response = await self.client.post(
            f"{self.API}/im/v1/messages",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"receive_id": recipient_recruiter_id, "msg_type": "interactive", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.is_error:
            # Keep the provider code/message (but never the request body) so retry
            # records explain actionable Feishu configuration/identity failures.
            code = data.get("code")
            message = str(data.get("msg") or data.get("message") or response.reason_phrase).strip()[:160]
            raise RuntimeError(f"Feishu send error: http={response.status_code}, code={code}, msg={message}")
        if data.get("code") != 0:
            message = str(data.get("msg") or data.get("message") or "unknown error").strip()[:160]
            raise RuntimeError(f"Feishu send error: code={data.get('code')}, msg={message}")
        message_id = (data.get("data") or {}).get("message_id")
        if not message_id:
            raise RuntimeError("Feishu send error: response missing message_id")
        return SendResult(message_id)

    async def aclose(self) -> None:
        await self.client.aclose()


class FeishuDirectoryError(RuntimeError):
    """Provider error with a safe Feishu code for controlled fallback logic."""

    def __init__(self, stage: str, code: object = None, status_code: int | None = None):
        self.stage = stage
        self.code = str(code) if code is not None else "unknown"
        self.status_code = status_code
        super().__init__(f"FEISHU_DIRECTORY_{stage}_ERROR:{self.code}")


class FeishuDirectoryClient:
    """Read every active member inside the Feishu app's contact scope.

    Feishu does not expose a single "all employees" endpoint.  The supported
    flow is: read the app contact scope, expand every authorized department via
    the children endpoint, then page through each department's direct users.
    Explicitly authorized users (not attached to an authorized department) are
    fetched in batches as well.  No directory result is persisted here.
    """

    API = "https://open.feishu.cn/open-apis"
    # Contact v3 list endpoints currently cap page_size at 50.  Passing 100
    # produces provider validation error 99992402 in production.
    PAGE_SIZE = 50
    MAX_PAGES = 1000
    USER_BATCH_SIZE = 50

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise ValueError("飞书通讯录需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        self.settings = settings
        self.client = client
        self._owned_client: httpx.AsyncClient | None = None

    @staticmethod
    def _body(response: httpx.Response, stage: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise FeishuDirectoryError(stage, f"HTTP_{response.status_code}", response.status_code) from exc
        if response.is_error:
            raise FeishuDirectoryError(stage, body.get("code"), response.status_code)
        if body.get("code") != 0:
            raise FeishuDirectoryError(stage, body.get("code"), response.status_code)
        return body

    @classmethod
    def _next_page(cls, data: dict[str, Any], stage: str, previous: str) -> str | None:
        if not data.get("has_more"):
            return None
        token = str(data.get("page_token") or "").strip()
        if not token or token == previous:
            raise FeishuDirectoryError(stage, "PAGINATION_TOKEN_MISSING")
        return token

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, *, stage: str, headers: dict[str, str], params: Any = None, json: Any = None) -> dict[str, Any]:
        response = await client.request(method, url, headers=headers, params=params, json=json)
        return self._body(response, stage)

    async def _tenant_token(self, client: httpx.AsyncClient) -> str:
        body = await self._request(
            client,
            "POST",
            f"{self.API}/auth/v3/tenant_access_token/internal",
            stage="TOKEN",
            headers={},
            json={"app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret},
        )
        token = str(body.get("tenant_access_token") or "").strip()
        if not token:
            raise FeishuDirectoryError("TOKEN", "MISSING_TOKEN")
        return token

    async def _contact_scope(self, client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[set[str], set[str]]:
        department_ids: set[str] = set()
        user_ids: set[str] = set()
        page_token = ""
        for _ in range(self.MAX_PAGES):
            params: dict[str, str | int] = {
                "user_id_type": "open_id",
                "department_id_type": "open_department_id",
                "page_size": self.PAGE_SIZE,
            }
            if page_token:
                params["page_token"] = page_token
            body = await self._request(client, "GET", f"{self.API}/contact/v3/scopes", stage="SCOPE", headers=headers, params=params)
            data = body.get("data") or {}
            department_ids.update(str(value).strip() for value in data.get("department_ids") or [] if str(value).strip())
            user_ids.update(str(value).strip() for value in data.get("user_ids") or [] if str(value).strip())
            next_token = self._next_page(data, "SCOPE", page_token)
            if next_token is None:
                return department_ids, user_ids
            page_token = next_token
        raise FeishuDirectoryError("SCOPE", "PAGE_LIMIT")

    async def _children(self, client: httpx.AsyncClient, headers: dict[str, str], department_id: str) -> set[str]:
        children: set[str] = set()
        page_token = ""
        endpoint = f"{self.API}/contact/v3/departments/{quote(department_id, safe='')}/children"
        for _ in range(self.MAX_PAGES):
            params: dict[str, str | int] = {
                "user_id_type": "open_id",
                "department_id_type": "open_department_id",
                "fetch_child": "true",
                "page_size": self.PAGE_SIZE,
            }
            if page_token:
                params["page_token"] = page_token
            body = await self._request(client, "GET", endpoint, stage="DEPARTMENTS", headers=headers, params=params)
            data = body.get("data") or {}
            for item in data.get("items") or []:
                child = str(item.get("open_department_id") or item.get("department_id") or "").strip()
                if child:
                    children.add(child)
            next_token = self._next_page(data, "DEPARTMENTS", page_token)
            if next_token is None:
                return children
            page_token = next_token
        raise FeishuDirectoryError("DEPARTMENTS", "PAGE_LIMIT")

    @staticmethod
    def _normalize_user(item: dict[str, Any], query: str) -> dict[str, str | None] | None:
        status = item.get("status") or {}
        if isinstance(status, dict) and any(status.get(flag) is True for flag in ("is_deleted", "is_resigned", "is_exited")):
            return None
        open_id = str(item.get("open_id") or "").strip()
        name = str(item.get("name") or item.get("nickname") or item.get("en_name") or "").strip()
        if open_id and not name:
            # Feishu still returns identifiers when the app has contact scope
            # but lacks the user basic-field permission.  Silently dropping
            # those people would again make a partial list look complete.
            raise FeishuDirectoryError("USER_FIELDS", "NAME_PERMISSION_MISSING")
        searchable = " ".join(str(item.get(key) or "") for key in ("name", "en_name", "nickname", "email")).casefold()
        if not open_id or not name or (query and query.casefold() not in searchable):
            return None
        return {
            "open_id": open_id,
            "user_id": str(item.get("user_id") or "").strip() or None,
            "display_name": name,
        }

    async def _department_users(self, client: httpx.AsyncClient, headers: dict[str, str], department_id: str, query: str, output: dict[str, dict[str, str | None]]) -> None:
        page_token = ""
        for _ in range(self.MAX_PAGES):
            params: dict[str, str | int] = {
                "department_id": department_id,
                "department_id_type": "open_department_id",
                "page_size": self.PAGE_SIZE,
                "user_id_type": "open_id",
            }
            if page_token:
                params["page_token"] = page_token
            body = await self._request(client, "GET", f"{self.API}/contact/v3/users/find_by_department", stage="USERS", headers=headers, params=params)
            data = body.get("data") or {}
            for item in data.get("items") or []:
                normalized = self._normalize_user(item, query)
                if normalized:
                    output[str(normalized["open_id"])] = normalized
            next_token = self._next_page(data, "USERS", page_token)
            if next_token is None:
                return
            page_token = next_token
        raise FeishuDirectoryError("USERS", "PAGE_LIMIT")

    async def _explicit_users(self, client: httpx.AsyncClient, headers: dict[str, str], user_ids: set[str], query: str, output: dict[str, dict[str, str | None]]) -> None:
        values = sorted(user_ids)
        for start in range(0, len(values), self.USER_BATCH_SIZE):
            chunk = values[start : start + self.USER_BATCH_SIZE]
            params: list[tuple[str, str | int]] = [("user_ids", value) for value in chunk]
            params.extend((("user_id_type", "open_id"), ("department_id_type", "open_department_id")))
            body = await self._request(client, "GET", f"{self.API}/contact/v3/users/batch", stage="USERS", headers=headers, params=params)
            for item in (body.get("data") or {}).get("items") or []:
                normalized = self._normalize_user(item, query)
                if normalized:
                    output[str(normalized["open_id"])] = normalized

    async def _run(self, query: str = "") -> list[dict[str, str | None]]:
        client = self.client
        if client is None:
            self._owned_client = httpx.AsyncClient(timeout=httpx.Timeout(10))
            client = self._owned_client
        token = await self._tenant_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        scoped_departments, scoped_users = await self._contact_scope(client, headers)
        # A scope response contains first-level departments for a full-tenant
        # scope, or explicitly selected departments for a partial scope.  The
        # children endpoint expands both cases without querying an unauthorized
        # root department.
        department_ids = set(scoped_departments)
        for department_id in tuple(scoped_departments):
            department_ids.update(await self._children(client, headers, department_id))
        output: dict[str, dict[str, str | None]] = {}
        for department_id in sorted(department_ids):
            await self._department_users(client, headers, department_id, query, output)
        # Root-level direct users exist only in a full/root scope.  Try them
        # opportunistically; a partial scope legitimately returns 40004 and
        # must not make the rest of the authorized directory fail.
        try:
            await self._department_users(client, headers, "0", query, output)
        except FeishuDirectoryError as exc:
            if exc.code not in {"40004", "40014", "41050"}:
                raise
        await self._explicit_users(client, headers, scoped_users, query, output)
        return sorted(output.values(), key=lambda item: (str(item.get("display_name") or "").casefold(), str(item.get("open_id") or "")))

    async def list_users(self, query: str = "") -> list[dict[str, str | None]]:
        try:
            return await self._run(query.strip())
        finally:
            if self._owned_client:
                await self._owned_client.aclose()
                self._owned_client = None


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
        # The browsing card goes to the viewer only, so it is written in the
        # second person; a "current viewer" line would just echo the
        # recipient's own name back at them.
        context = f"**岗位**：{payload.get('job_name', '未知')}\n" if browsing else ""
        elements: list[dict[str, Any]] = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**候选人**：{payload.get('candidate_name', '未知')}\n{context}**已有招聘者**：{payload.get('matched_recruiter_name', '未知')}\n**首次沟通**：{payload.get('first_contact_at') or '未知'}\n**最近活动**：{payload.get('last_activity_at') or '未知'}\n**匹配依据**：{payload.get('match_reason', '未知')}\n{payload.get('current_action', '')}\n{'当前仅浏览，尚未确认已发送消息。' if browsing else '请人工核对，不会自动合并。'}",
                },
            }
        ]
        # Notifications are intentionally informational only.  Do not add
        # interactive action buttons: they require a Feishu callback service
        # and the user does not need in-card workflow controls here.
        return {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": title}, "template": "orange"}, "elements": elements}

    @staticmethod
    def _action_value(action: str, payload: dict[str, Any]) -> dict[str, str]:
        value = {"action": action}
        conflict_id = str(payload.get("conflict_id") or "").strip()
        if conflict_id:
            value["conflict_id"] = conflict_id
        return value


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
