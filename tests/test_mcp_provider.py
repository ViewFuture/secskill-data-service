"""MCP Jobs Provider 路由与映射测试（禁止真实出网 / 禁止打印 Token）。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import app as app_module

COLLECT_PATH = "/plugin/v1/jobs/collect"
BODY = {
    "keywords": "网络安全运维",
    "region": "广东",
    "start_date": "2026-05-01",
    "end_date": "2026-08-01",
    "max_items": 20,
}

ADAPTER_FIXTURE = {
    "provider": "mcp-jobs",
    "provider_version": "1.4.0",
    "mode": "fixture",
    "jobs": [
        {
            "job_title": "网络安全运维工程师",
            "company": "演示企业A",
            "region": "广东佛山",
            "publish_date": "2026-06-01",
            "description": "运维",
            "skills": ["Linux"],
            "source_url": "https://example.org/j1",
        },
        {
            "job_title": "安全运营工程师",
            "company": "演示企业B",
            "region": "广东广州",
            "publish_date": "2026-06-10",
            "description": "运营",
            "skills": ["SIEM"],
            "source_url": "https://example.org/j2",
        },
        {
            "job_title": "SOC分析师",
            "company": "演示企业C",
            "region": "广东深圳",
            "publish_date": "2026-06-15",
            "description": "SOC",
            "skills": ["SOC"],
            "source_url": "https://example.org/j3",
        },
    ],
    "source_ledger": [
        {"source_code": "mcp_fixture", "status": "ok", "fetched": 3},
    ],
    "warnings": [
        "FIXTURE_DATA_NOT_FOR_REAL_TREND_CLAIMS",
        "UNKNOWN_SOURCE_DOMAIN",
    ],
}


def _enable_mcp(monkeypatch: pytest.MonkeyPatch, *, fallback: bool = True) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("JOB_PROVIDER", " mcp_jobs ")
    monkeypatch.setenv("MCP_JOBS_ENABLED", "true")
    monkeypatch.setenv(
        "MCP_JOBS_BASE_URL",
        "https://secskill-mcp-jobs-adapter.onrender.com/",
    )
    monkeypatch.setenv("MCP_JOBS_TOKEN", "adapter-internal-token-for-tests")
    monkeypatch.setenv("MCP_JOBS_FALLBACK_TO_DEMO", "true" if fallback else "false")
    monkeypatch.setenv("MCP_JOBS_PAGE", "1")

    monkeypatch.setattr(app_module, "DEMO_MODE", False)
    monkeypatch.setattr(app_module, "JOB_PROVIDER", "mcp_jobs")
    monkeypatch.setattr(app_module, "MCP_JOBS_ENABLED", True)
    monkeypatch.setattr(
        app_module,
        "MCP_JOBS_BASE_URL",
        "https://secskill-mcp-jobs-adapter.onrender.com",
    )
    monkeypatch.setattr(app_module, "MCP_JOBS_TOKEN", "adapter-internal-token-for-tests")
    monkeypatch.setattr(app_module, "MCP_JOBS_FALLBACK_TO_DEMO", fallback)
    monkeypatch.setattr(app_module, "MCP_JOBS_PAGE", 1)
    monkeypatch.setattr(app_module, "MCP_JOBS_TIMEOUT_SECONDS", 5.0)


def _parse(response) -> dict:
    payload = response.json()
    assert "result" in payload
    assert isinstance(payload["result"], str)
    return json.loads(payload["result"])


def test_parse_bool_true_false_strings():
    assert app_module._parse_bool("true") is True
    assert app_module._parse_bool("TRUE") is True
    assert app_module._parse_bool(" false ") is False
    assert app_module._parse_bool("0") is False
    assert app_module._parse_bool("no") is False
    assert app_module._parse_bool("") is False


def test_demo_mode_true_does_not_call_adapter(client, auth_headers, monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_MODE", True)
    with patch.object(
        app_module, "call_mcp_jobs_adapter", new_callable=AsyncMock
    ) as mocked:
        response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    assert response.status_code == 200
    inner = _parse(response)
    assert inner["data_mode"] == "demo"
    mocked.assert_not_called()


def test_mcp_provider_called_when_enabled(client, auth_headers, monkeypatch):
    _enable_mcp(monkeypatch)
    with patch.object(
        app_module,
        "call_mcp_jobs_adapter",
        new_callable=AsyncMock,
        return_value=(ADAPTER_FIXTURE, 200),
    ) as mocked:
        with patch.object(
            app_module, "_collect_live", new_callable=AsyncMock
        ) as live_mock:
            response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    assert response.status_code == 200
    mocked.assert_awaited_once()
    live_mock.assert_not_called()
    inner = _parse(response)
    assert inner["data_mode"] == "mcp_adapter_fixture"
    assert inner["count"] == 3
    assert inner["provider"] == "mcp-jobs"
    assert inner["provider_version"] == "1.4.0"
    assert inner["source_ledger"] == ADAPTER_FIXTURE["source_ledger"]
    assert inner["warnings"] == ADAPTER_FIXTURE["warnings"]


def test_mcp_live_mode_maps_to_live_public_mcp(client, auth_headers, monkeypatch):
    _enable_mcp(monkeypatch)
    live_body = {**ADAPTER_FIXTURE, "mode": "live"}
    with patch.object(
        app_module,
        "call_mcp_jobs_adapter",
        new_callable=AsyncMock,
        return_value=(live_body, 200),
    ):
        response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    inner = _parse(response)
    assert inner["data_mode"] == "live_public_mcp"
    assert inner["provider"] == "mcp-jobs"


def test_mcp_failure_fallback_demo(client, auth_headers, monkeypatch):
    _enable_mcp(monkeypatch, fallback=True)
    with patch.object(
        app_module,
        "call_mcp_jobs_adapter",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        with patch.object(
            app_module, "_collect_live", new_callable=AsyncMock
        ) as live_mock:
            response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    live_mock.assert_not_called()
    inner = _parse(response)
    assert inner["data_mode"] == "fallback_demo"
    assert "FALLBACK_TO_DEMO_AFTER_MCP_FAILURE" in inner["warnings"]


def test_mcp_failure_without_fallback(client, auth_headers, monkeypatch):
    _enable_mcp(monkeypatch, fallback=False)
    with patch.object(
        app_module,
        "call_mcp_jobs_adapter",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        with patch.object(
            app_module, "_collect_live", new_callable=AsyncMock
        ) as live_mock:
            response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    live_mock.assert_not_called()
    inner = _parse(response)
    assert inner["data_mode"] == "live_public_mcp_failed"
    assert "MCP_JOBS_ADAPTER_FAILED" in inner["warnings"]


def test_non_mcp_provider_uses_public_sources(client, auth_headers, monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_MODE", False)
    monkeypatch.setattr(app_module, "JOB_PROVIDER", "public_sources")
    monkeypatch.setattr(app_module, "MCP_JOBS_ENABLED", True)
    with patch.object(
        app_module, "call_mcp_jobs_adapter", new_callable=AsyncMock
    ) as mocked:
        with patch.object(
            app_module,
            "_collect_live",
            new_callable=AsyncMock,
            return_value=app_module.ToolResponse(
                result=json.dumps(
                    {
                        "count": 0,
                        "data_mode": "live_public",
                        "provider": None,
                        "provider_version": None,
                        "batch_preview": {},
                        "raw_items": [],
                        "source_ledger": [],
                        "warnings": ["NO_ENABLED_PUBLIC_SOURCES"],
                    }
                )
            ),
        ) as live_mock:
            response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    mocked.assert_not_called()
    live_mock.assert_awaited_once()
    assert response.status_code == 200


def test_mcp_disabled_does_not_call_adapter(client, auth_headers, monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_MODE", False)
    monkeypatch.setattr(app_module, "JOB_PROVIDER", "mcp_jobs")
    monkeypatch.setattr(app_module, "MCP_JOBS_ENABLED", False)
    with patch.object(
        app_module, "call_mcp_jobs_adapter", new_callable=AsyncMock
    ) as mocked:
        with patch.object(
            app_module,
            "_collect_live",
            new_callable=AsyncMock,
            return_value=app_module.ToolResponse(
                result=json.dumps(
                    {
                        "count": 0,
                        "data_mode": "live_public",
                        "provider": None,
                        "provider_version": None,
                        "batch_preview": {},
                        "raw_items": [],
                        "source_ledger": [],
                        "warnings": ["NO_ENABLED_PUBLIC_SOURCES"],
                    }
                )
            ),
        ):
            response = client.post(COLLECT_PATH, headers=auth_headers, json=BODY)
    mocked.assert_not_called()
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_adapter_request_url_and_header_without_logging_token(monkeypatch):
    _enable_mcp(monkeypatch)
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return ADAPTER_FIXTURE

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    request = app_module.CollectPublicJobsRequest(**BODY)
    with patch.object(app_module.httpx, "AsyncClient", FakeClient):
        body, status_code = await app_module.call_mcp_jobs_adapter(request)

    assert status_code == 200
    assert body["provider"] == "mcp-jobs"
    assert captured["url"].endswith("/internal/v1/jobs/search")
    assert captured["url"].count("/internal/v1/jobs/search") == 1
    assert "//internal" not in captured["url"]
    assert captured["headers"]["X-Internal-Token"] == "adapter-internal-token-for-tests"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["keyword"] == BODY["keywords"]
    assert captured["json"]["city"] == BODY["region"]
    assert captured["json"]["page"] == 1
    assert captured["json"]["max_items"] == 20
    # 不在测试输出中打印 Token；仅断言 header 键存在
    assert "X-Internal-Token" in captured["headers"]


def test_openapi_contract_unchanged(client):
    schema = app_module.app.openapi()
    post = schema["paths"]["/plugin/v1/jobs/collect"]["post"]
    assert post["operationId"] == "collectPublicJobs"
    ref = post["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in ref:
        name = ref["$ref"].split("/")[-1]
        result_schema = schema["components"]["schemas"][name]["properties"]["result"]
    else:
        result_schema = ref["properties"]["result"]
    assert result_schema["type"] == "string"
