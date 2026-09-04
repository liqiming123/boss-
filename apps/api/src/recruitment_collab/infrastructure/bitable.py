from __future__ import annotations

from typing import Any

import httpx

from recruitment_collab.config.settings import Settings
from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer


class BitableSyncClient:
    API = "https://open.feishu.cn/open-apis"
    REQUIRED_FIELDS = {
        "候选人": 1,
        "候选人标识": 1,
        "BOSS岗位": 1,
        "当前招聘者": 1,
        "开始聊天时间": 5,
        "更新时间": 5,
        "状态": 1,
        "聊天框截图": 17,
        "简历附件": 17,
    }
    # Only these fields are written to the user-facing candidate table.  The
    # application may still carry additional identity/evidence fields
    # internally for matching and audit, but they must not leak into the
    # presentation table.
    VISIBLE_FIELDS = frozenset(REQUIRED_FIELDS)
    # Feishu creates the first column as "文本" by default. Treat it as the
    # legacy candidate-name column so schema reconciliation renames it instead
    # of leaving the primary column blank while creating a second name field.
    LEGACY_FIELD_NAMES = {
        "候选人": "文本",
        "开始聊天时间": "聊天时间",
        "聊天框截图": "当前对话快照",
    }
    REMOVED_CHAT_FIELDS = frozenset(
        {"聊天摘要状态", "招聘者消息数", "候选人回复数", "最后招聘者消息时间", "最后候选人消息时间", "最近聊天动作"}
    )
    DATETIME_PROPERTY = {"auto_fill": False, "date_formatter": "yyyy/MM/dd HH:mm"}

    @staticmethod
    def plain_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return "".join(BitableSyncClient.plain_value(item) for item in value)
        if isinstance(value, dict):
            return str(value.get("text") or value.get("name") or value.get("value") or "")
        return str(value)

    def __init__(self, settings: Settings):
        self.settings = settings
        self._schema_checked = False
        self._existing_field_names: set[str] = set()

    def _table_url(self, suffix: str) -> str:
        return f"{self.API}/bitable/v1/apps/{self.settings.feishu_bitable_app_token}/tables/{self.settings.feishu_bitable_candidate_table_id}/{suffix}"

    @staticmethod
    def _data(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Keep the Feishu error code/message (never the response body) so
            # upload failures can be diagnosed without leaking payloads.
            try:
                body = response.json()
                code, message = body.get("code"), body.get("msg")
                raise RuntimeError(f"FEISHU_HTTP_ERROR:{code}:{message}") from exc
            except ValueError:
                raise
        body = response.json()
        if body.get("code", 0) != 0:
            raise RuntimeError(f"FEISHU_API_ERROR:{body.get('code')}")
        return body.get("data", {})

    def _ensure_candidate_fields(self, client: httpx.Client, headers: dict[str, str]) -> None:
        if self._schema_checked:
            return
        fields_base = self._table_url("fields")
        existing = self._list_fields(client, headers)
        self._existing_field_names = set(existing)
        for name in self.REMOVED_CHAT_FIELDS:
            field = existing.get(name)
            if field and field.get("field_id"):
                self._data(client.delete(f"{fields_base}/{field['field_id']}", headers=headers))
        for name, field_type in self.REQUIRED_FIELDS.items():
            definition = self._field_definition(name, field_type)
            field = existing.get(name)
            legacy_name = self.LEGACY_FIELD_NAMES.get(name)
            if not field and legacy_name and legacy_name in existing:
                field = self._rename_field(client, headers, existing[legacy_name], definition)
            if field:
                self._validate_field(client, headers, field, definition)
            else:
                self._data(client.post(fields_base, headers=headers, json=definition))
        self._existing_field_names.update(self.REQUIRED_FIELDS)
        self._schema_checked = True

    def _list_fields(self, client: httpx.Client, headers: dict[str, str]) -> dict[str, dict[str, Any]]:
        fields_base = self._table_url("fields")
        existing: dict[str, dict[str, Any]] = {}
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._data(client.get(fields_base, headers=headers, params=params))
            existing.update({item["field_name"]: item for item in data.get("items", [])})
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                raise RuntimeError("FEISHU_PAGINATION_TOKEN_MISSING")
        return existing

    def _field_definition(self, name: str, field_type: int) -> dict[str, Any]:
        definition: dict[str, Any] = {"field_name": name, "type": field_type}
        if field_type == 5:
            definition["property"] = self.DATETIME_PROPERTY
        return definition

    def _rename_field(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        field: dict[str, Any],
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        field_id = field.get("field_id")
        if not field_id:
            raise RuntimeError(f"FEISHU_FIELD_ID_MISSING:{field.get('field_name', '')}")
        self._data(client.put(f"{self._table_url('fields')}/{field_id}", headers=headers, json=definition))
        return {**field, **definition}

    def _validate_field(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        field: dict[str, Any],
        definition: dict[str, Any],
    ) -> None:
        name, field_type = definition["field_name"], definition["type"]
        if field.get("type") != field_type:
            raise RuntimeError(f"FEISHU_FIELD_TYPE_MISMATCH:{name}")
        if field_type != 5 or field.get("property", {}).get("date_formatter") == self.DATETIME_PROPERTY["date_formatter"]:
            return
        field_id = field.get("field_id")
        if not field_id:
            raise RuntimeError(f"FEISHU_FIELD_ID_MISSING:{name}")
        self._data(client.put(f"{self._table_url('fields')}/{field_id}", headers=headers, json=definition))

    def _headers(self, client: httpx.Client) -> dict[str, str]:
        token_response = client.post(
            f"{self.API}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret},
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        if token_data.get("code") != 0:
            raise RuntimeError(f"FEISHU_TOKEN_ERROR:{token_data.get('code')}")
        return {"Authorization": f"Bearer {token_data['tenant_access_token']}"}

    def _record_pages(self, client: httpx.Client, headers: dict[str, str]):
        base = self._table_url("records")
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data = self._data(client.get(base, headers=headers, params=params))
            yield from data.get("items", [])
            if not data.get("has_more"):
                return
            page_token = data.get("page_token")
            if not page_token:
                raise RuntimeError("FEISHU_PAGINATION_TOKEN_MISSING")

    def find_system_candidates(
        self, identity_signature: str | None, current_recruiter: str, candidate_name: str = "", job_name: str = "", canonical_job_name: str = ""
    ) -> list[dict[str, Any]]:
        """Read the Bitable directly; manual rows never participate in duplicate warnings."""
        if self.settings.feishu_mode != "real":
            return []
        with httpx.Client(timeout=15) as client:
            headers = self._headers(client)
            records = []
            normalized_name = CandidateNameNormalizer().normalize(candidate_name)
            normalized_jobs = {JobNameNormalizer().normalize(value) for value in (job_name, canonical_job_name) if value}
            for row in self._record_pages(client, headers):
                fields = row.get("fields", {})
                if not self.plain_value(fields.get("系统记录标识")):
                    continue
                recruiter = self.plain_value(fields.get("当前招聘者")).strip()
                if not recruiter or recruiter == current_recruiter.strip():
                    continue
                exact = bool(identity_signature) and self.plain_value(fields.get("候选人身份签名")) == identity_signature
                row_name = CandidateNameNormalizer().normalize(self.plain_value(fields.get("候选人")))
                row_jobs = {
                    JobNameNormalizer().normalize(self.plain_value(fields.get(name))) for name in ("BOSS岗位", "标准岗位") if self.plain_value(fields.get(name))
                }
                same_name_job = bool(normalized_name and normalized_jobs and row_name == normalized_name and not normalized_jobs.isdisjoint(row_jobs))
                if not exact and not same_name_job:
                    continue
                records.append({"record_id": row.get("record_id"), "match_level": "EXACT_IDENTITY" if exact else "SUSPECTED_SAME_NAME_JOB", **fields})
            return records

    def upload_snapshot(self, file_name: str, content: bytes) -> str:
        """Upload one app-owned image for use in the configured Bitable attachment field."""
        if self.settings.feishu_mode != "real":
            return f"mock-{file_name}"
        with httpx.Client(timeout=30) as client:
            headers = self._headers(client)
            data = self._data(
                client.post(
                    f"{self.API}/drive/v1/medias/upload_all",
                    headers=headers,
                    data={
                        "file_name": file_name,
                        # The destination column is a Bitable attachment
                        # field (type 17), not an image column.  Feishu
                        # rejects bitable_image uploads for this field with
                        # HTTP 400; bitable_file tokens are accepted by the
                        # attachment field and render in the table.
                        "parent_type": "bitable_file",
                        "parent_node": self.settings.feishu_bitable_app_token,
                        "size": str(len(content)),
                    },
                    files={"file": (file_name, content, "image/jpeg")},
                )
            )
            token = data.get("file_token")
            if not token:
                raise RuntimeError("FEISHU_SNAPSHOT_TOKEN_MISSING")
            return str(token)

    def upload_resume(self, file_name: str, content: bytes, content_type: str) -> str:
        """Upload only after the user explicitly opens the BOSS attachment preview."""
        if self.settings.feishu_mode != "real":
            return f"mock-resume-{file_name}"
        with httpx.Client(timeout=60) as client:
            headers = self._headers(client)
            data = self._data(
                client.post(
                    f"{self.API}/drive/v1/medias/upload_all",
                    headers=headers,
                    data={
                        "file_name": file_name,
                        "parent_type": "bitable_file",
                        "parent_node": self.settings.feishu_bitable_app_token,
                        "size": str(len(content)),
                    },
                    files={"file": (file_name, content, content_type)},
                )
            )
            token = data.get("file_token")
            if not token:
                raise RuntimeError("FEISHU_RESUME_TOKEN_MISSING")
            return str(token)

    def upsert_candidate(self, fields: dict[str, Any], record_id: str | None = None) -> tuple[str, str | None]:
        if self.settings.feishu_mode != "real" or not self.settings.feishu_bitable_app_token or not self.settings.feishu_bitable_candidate_table_id:
            return "SKIPPED", record_id
        with httpx.Client(timeout=15) as client:
            headers = {**self._headers(client), "Content-Type": "application/json"}
            self._ensure_candidate_fields(client, headers)
            base = self._table_url("records")
            # Keep internal fields available for duplicate matching, while
            # writing only the curated, user-facing projection to Feishu.
            display_fields = {
                name: value
                for name, value in fields.items()
                if name in self.VISIBLE_FIELDS and value is not None
            }
            # Existing tables may still have Feishu's original primary
            # column named “文本”. Populate it as well as the curated
            # “候选人” column so the first visible column is never blank.
            if "文本" in self._existing_field_names and fields.get("候选人"):
                display_fields["文本"] = fields["候选人"]
            if record_id:
                self._data(client.put(f"{base}/{record_id}", headers=headers, json={"fields": display_fields}))
                return "UPDATED", record_id
            key_name = "会话岗位标识" if fields.get("会话岗位标识") else "候选人标识"
            existing = next(
                (row for row in self._record_pages(client, headers) if row.get("fields", {}).get(key_name) == fields.get(key_name)),
                None,
            )
            if existing:
                self._data(client.put(f"{base}/{existing['record_id']}", headers=headers, json={"fields": display_fields}))
                return "UPDATED", existing["record_id"]
            response = client.post(base, headers=headers, json={"fields": display_fields})
            data = self._data(response)
            return "CREATED", data.get("record", {}).get("record_id")
