"""插件鉴权相关测试。"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

import app as app_module
from tests.conftest import TEST_TOKEN

COLLECT_PATH = "/plugin/v1/jobs/collect"
VALID_BODY = {
    "keywords": "SOC",
    "region": "",
    "start_date": "2026-05-01",
    "end_date": "2026-08-01",
    "max_items": 20,
}


def test_collect_without_authorization_returns_401(client):
    response = client.post(COLLECT_PATH, json=VALID_BODY)
    assert response.status_code == 401, response.text


def test_collect_with_wrong_token_returns_401(client):
    response = client.post(
        COLLECT_PATH,
        headers={"Authorization": "Bearer wrong-token"},
        json=VALID_BODY,
    )
    assert response.status_code == 401, response.text


def test_collect_with_correct_bearer_token_enters_endpoint(client, auth_headers):
    response = client.post(COLLECT_PATH, headers=auth_headers, json=VALID_BODY)
    assert response.status_code == 200, response.text
    assert "result" in response.json()


def test_collect_without_plugin_token_configured_returns_500(
    monkeypatch: pytest.MonkeyPatch,
    configured_env,
):
    monkeypatch.delenv("PLUGIN_TOKEN", raising=False)
    monkeypatch.setattr(app_module, "PLUGIN_TOKEN", None)
    client = TestClient(app_module.app)

    response = client.post(
        COLLECT_PATH,
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        json=VALID_BODY,
    )
    assert response.status_code == 500, response.text
    assert TEST_TOKEN not in response.text


def test_token_plaintext_not_leaked_in_response_or_logs(
    client,
    auth_headers,
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.DEBUG):
        ok = client.post(COLLECT_PATH, headers=auth_headers, json=VALID_BODY)
        bad = client.post(
            COLLECT_PATH,
            headers={"Authorization": "Bearer wrong-token"},
            json=VALID_BODY,
        )
        missing = client.post(COLLECT_PATH, json=VALID_BODY)

    assert ok.status_code == 200, ok.text
    assert bad.status_code == 401, bad.text
    assert missing.status_code == 401, missing.text

    combined = "\n".join(
        [
            ok.text,
            bad.text,
            missing.text,
            caplog.text,
        ]
    )
    assert TEST_TOKEN not in combined, "正确 Token 明文不应出现在响应或日志中"
