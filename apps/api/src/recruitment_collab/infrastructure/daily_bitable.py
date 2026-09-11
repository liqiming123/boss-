from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from recruitment_collab.infrastructure.bitable import BitableSyncClient

METRIC_FIELDS = {
    "boss_viewed_talent": "BOSS查看牛人", "boss_started_chat": "BOSS发起聊天",
    "boss_communication": "BOSS沟通", "talent_viewed_boss": "牛人查看BOSS",
    "talent_started_chat": "牛人发起聊天",
}


class DailyBitableClient(BitableSyncClient):
    # This subclass never runs candidate-table cleanup or creates candidate fields.
    REQUIRED_FIELDS = {"BOSS姓名": 1, "日期": 5, **{name: 2 for name in METRIC_FIELDS.values()}}
    DATETIME_PROPERTY = {"auto_fill": False, "date_formatter": "yyyy/MM/dd"}

    def ensure_fields(self, client, headers):
        existing = self._list_fields(client, headers)
        for name, field_type in self.REQUIRED_FIELDS.items():
            definition = self._field_definition(name, field_type)
            field = existing.get(name)
            if not field and name == "BOSS姓名" and "文本" in existing:
                field = self._rename_field(client, headers, existing["文本"], definition)
            if field:
                self._validate_field(client, headers, field, definition)
            else:
                self._data(client.post(self._table_url("fields"), headers=headers, json=definition))

    def configure(self):
        with httpx.Client(timeout=30) as client:
            headers = self._headers(client)
            canonical = self._resolve_media_parent_node(client, headers)
            self.ensure_fields(client, headers)
            return {"app_token": canonical, "table_id": self.candidate_table_id, "fields": list(self.REQUIRED_FIELDS)}

    def upsert_daily(self, metric_date: str, boss_name: str, metrics: dict[str, int], record_id=None):
        stamp = int(datetime.strptime(metric_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        fields: dict[str, Any] = {"日期": stamp, "BOSS姓名": boss_name, **{name: metrics[key] for key, name in METRIC_FIELDS.items()}}
        if self.settings.feishu_mode != "real":
            return record_id or f"mock-daily-{metric_date}-{boss_name}"
        with httpx.Client(timeout=30) as client:
            headers = self._headers(client)
            self.ensure_fields(client, headers)
            if not record_id:
                matches = []
                for row in self._record_pages(client, headers):
                    saved = row.get("fields", {})
                    value = saved.get("日期")
                    if self.plain_value(saved.get("BOSS姓名")) != boss_name or not isinstance(value, (int, float)):
                        continue
                    saved_date = datetime.fromtimestamp(value / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                    if saved_date == metric_date:
                        matches.append(row["record_id"])
                if len(matches) > 1:
                    raise RuntimeError("DAILY_TABLE_DUPLICATE_DATE_NAME")
                record_id = matches[0] if matches else None
            base = self._table_url("records")
            response = client.put(f"{base}/{record_id}", headers=headers, json={"fields": fields}) if record_id else client.post(base, headers=headers, json={"fields": fields})
            return str(self._data(response)["record"]["record_id"])
