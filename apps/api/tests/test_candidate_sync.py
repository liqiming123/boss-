from __future__ import annotations

from urllib.parse import parse_qs

import httpx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from recruitment_collab.config.settings import get_settings
from recruitment_collab.infrastructure.bitable import BitableSyncClient
from recruitment_collab.infrastructure.models import CandidateSource, CandidateSyncOutbox
from recruitment_collab.workers import candidate_sync_worker


def test_bitable_upsert_searches_all_record_pages(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "feishu_mode": "real",
            "feishu_app_id": "app",
            "feishu_app_secret": "secret",
            "feishu_bitable_app_token": "base",
            "feishu_bitable_candidate_table_id": "table",
        }
    )
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if path.endswith("/fields") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "field_name": name,
                                "field_id": f"field-{index}",
                                "type": field_type,
                                "property": BitableSyncClient.DATETIME_PROPERTY if field_type == 5 else {},
                            }
                            for index, (name, field_type) in enumerate(BitableSyncClient.REQUIRED_FIELDS.items())
                        ],
                        "has_more": False,
                    },
                },
            )
        if path.endswith("/records") and request.method == "GET":
            page = parse_qs(request.url.query.decode()).get("page_token", [""])[0]
            requested_pages.append(page)
            if not page:
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"items": [{"record_id": "other", "fields": {"候选人标识": "other"}}], "has_more": True, "page_token": "page-2"}},
                )
            return httpx.Response(
                200, json={"code": 0, "data": {"items": [{"record_id": "target", "fields": {"候选人标识": "candidate-key"}}], "has_more": False}}
            )
        if path.endswith("/records/target") and request.method == "PUT":
            return httpx.Response(200, json={"code": 0, "data": {}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("recruitment_collab.infrastructure.bitable.httpx.Client", lambda **kwargs: real_client)
    status, record_id = BitableSyncClient(settings).upsert_candidate({"候选人标识": "candidate-key"})
    assert (status, record_id) == ("UPDATED", "target")
    assert requested_pages == ["", "page-2"]


def test_bitable_updates_datetime_fields_to_include_hours_and_minutes(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "feishu_mode": "real",
            "feishu_app_id": "app",
            "feishu_app_secret": "secret",
            "feishu_bitable_app_token": "base",
            "feishu_bitable_candidate_table_id": "table",
        }
    )
    updated: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if path.endswith("/fields") and request.method == "GET":
            items = [
                {"field_name": name, "field_id": f"field-{index}", "type": field_type, "property": {"date_formatter": "yyyy/MM/dd"} if field_type == 5 else {}}
                for index, (name, field_type) in enumerate(BitableSyncClient.REQUIRED_FIELDS.items())
            ]
            return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})
        if "/fields/field-" in path and request.method == "PUT":
            updated.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("/records") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        if path.endswith("/records") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "created"}}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("recruitment_collab.infrastructure.bitable.httpx.Client", lambda **kwargs: real_client)
    BitableSyncClient(settings).upsert_candidate({"候选人标识": "candidate-key"})
    assert {item["field_name"] for item in updated} == {"开始聊天时间", "更新时间"}
    assert all(item["property"]["date_formatter"] == "yyyy/MM/dd HH:mm" for item in updated)


def test_bitable_renames_legacy_business_fields_without_creating_duplicates(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "feishu_mode": "real",
            "feishu_app_id": "app",
            "feishu_app_secret": "secret",
            "feishu_bitable_app_token": "base",
            "feishu_bitable_candidate_table_id": "table",
        }
    )
    updated: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if path.endswith("/fields") and request.method == "GET":
            items = [
                {
                    "field_name": name,
                    "field_id": f"field-{index}",
                    "type": field_type,
                    "property": BitableSyncClient.DATETIME_PROPERTY if field_type == 5 else {},
                }
                for index, (name, field_type) in enumerate(BitableSyncClient.REQUIRED_FIELDS.items())
                if name not in BitableSyncClient.LEGACY_FIELD_NAMES
            ]
            items.extend(
                [
                    {"field_name": "文本", "field_id": "legacy-name", "type": 1, "property": {}},
                    {"field_name": "聊天时间", "field_id": "legacy-time", "type": 5, "property": {"date_formatter": "yyyy/MM/dd"}},
                    {"field_name": "当前对话快照", "field_id": "legacy-snapshot", "type": 17, "property": {}},
                ]
            )
            return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})
        if "/fields/legacy-" in path and request.method == "PUT":
            updated.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("/records") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        if path.endswith("/records") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "created"}}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("recruitment_collab.infrastructure.bitable.httpx.Client", lambda **kwargs: real_client)
    BitableSyncClient(settings).upsert_candidate({"候选人标识": "candidate-key"})
    assert {item["field_name"] for item in updated} == {"候选人", "开始聊天时间", "聊天框截图"}


def test_bitable_direct_lookup_only_returns_other_recruiters_system_rows(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "feishu_mode": "real",
            "feishu_app_id": "app",
            "feishu_app_secret": "secret",
            "feishu_bitable_app_token": "base",
            "feishu_bitable_candidate_table_id": "table",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/records"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "has_more": False,
                        "items": [
                            {"record_id": "manual", "fields": {"候选人身份签名": "sig", "当前招聘者": "甲"}},
                            {"record_id": "self", "fields": {"系统记录标识": "source-self", "候选人身份签名": "sig", "当前招聘者": "当前"}},
                            {
                                "record_id": "other",
                                "fields": {"系统记录标识": "source-other", "候选人身份签名": "sig", "当前招聘者": "甲", "BOSS岗位": "总助", "状态": "已约面"},
                            },
                            {"record_id": "different", "fields": {"系统记录标识": "source-different", "候选人身份签名": "other-sig", "当前招聘者": "乙"}},
                        ],
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("recruitment_collab.infrastructure.bitable.httpx.Client", lambda **kwargs: real_client)
    rows = BitableSyncClient(settings).find_system_candidates("sig", "当前")
    assert [(row["record_id"], row["当前招聘者"]) for row in rows] == [("other", "甲")]


def test_mock_worker_marks_queued_candidate_as_sent(client, session, monkeypatch):
    from conftest import login
    from test_api_flow import context, message_sent

    response = message_sent(client, login(client, "xie@example.com"), context("队列候选人", "谢女士", "queue"))
    assert response.status_code == 200
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    monkeypatch.setattr(candidate_sync_worker, "SessionLocal", factory)
    monkeypatch.setattr(candidate_sync_worker, "get_settings", lambda: get_settings().model_copy(update={"feishu_mode": "mock"}))
    assert candidate_sync_worker.process_batch() == 1
    session.expire_all()
    queued = session.scalar(select(CandidateSyncOutbox))
    source = session.scalar(select(CandidateSource))
    assert queued is not None and queued.status == "SENT"
    assert queued.payload_json == {}
    assert source is not None and source.feishu_record_id is None
