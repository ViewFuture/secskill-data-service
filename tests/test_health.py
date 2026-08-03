"""健康检查与根路径测试。"""

from __future__ import annotations


def test_root_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200, response.text


def test_root_returns_service_name(client):
    payload = client.get("/").json()
    assert payload.get("service") == "SecSkill_Data_Service", payload


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200, response.text


def test_health_returns_status_ok(client):
    payload = client.get("/health").json()
    assert payload.get("status") == "ok", payload
    assert payload.get("service") == "SecSkill_Data_Service", payload
