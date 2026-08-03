"""共享测试夹具：通过 monkeypatch 隔离环境变量与模块级配置。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module

TEST_TOKEN = "pytest-plugin-token-do-not-leak"


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def demo_jobs_payload() -> dict:
    """用于采集/去重/过滤联调的临时演示数据（非正式 fixtures）。"""
    return {
        "version": 1,
        "demo": True,
        "jobs": [
            {
                "job_title": "SOC分析师",
                "company": "演示企业A",
                "region": "广东深圳",
                "publish_date": "2026-06-01",
                "description": "负责SOC告警研判与日志分析",
                "skills": ["SOC", "SIEM", "告警研判"],
                "source_url": "https://example.org/demo/jobs/t001",
            },
            {
                "job_title": "网络安全运维工程师",
                "company": "演示企业B",
                "region": "广东佛山",
                "publish_date": "2026-05-10",
                "description": "负责防火墙与Linux运维",
                "skills": ["防火墙", "Linux", "WAF"],
                "source_url": "https://example.org/demo/jobs/t002",
            },
            {
                "job_title": "网络安全运维工程师",
                "company": "演示企业B",
                "region": "广东佛山",
                "publish_date": "2026-05-10",
                "description": "去重测试重复记录",
                "skills": ["防火墙", "Linux"],
                "source_url": "https://example.org/demo/jobs/t003",
            },
            {
                "job_title": "渗透测试工程师",
                "company": "演示企业C",
                "region": "广东广州",
                "publish_date": "2026-99-99",
                "description": "非法日期应被过滤",
                "skills": ["漏洞扫描", "Python"],
                "source_url": "https://example.org/demo/jobs/t004",
            },
            {
                "job_title": "安全运营工程师",
                "company": "演示企业D",
                "region": "广东广州",
                "publish_date": "2026-07-20",
                "description": "安全运营与SIEM值班",
                "skills": ["SIEM", "日志分析", "Python"],
                "source_url": "https://example.org/demo/jobs/t005",
            },
            {
                "job_title": "应急响应工程师",
                "company": "演示企业E",
                "region": "广东东莞",
                "publish_date": "2026-04-01",
                "description": "日期范围外样本",
                "skills": ["应急响应"],
                "source_url": "https://example.org/demo/jobs/t006",
            },
        ],
    }


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, demo_jobs_payload: dict):
    """设置测试环境：临时 fixtures/sources，并同步到 app 模块级变量。"""
    demo_file = _write_json(tmp_path / "jobs.json", demo_jobs_payload)
    source_file = _write_json(
        tmp_path / "sources.json",
        {
            "version": 1,
            "sources": [
                {
                    "id": "disabled-feed",
                    "name": "禁用源",
                    "type": "json_feed",
                    "url": "https://example.org/feed.json",
                    "enabled": False,
                }
            ],
        },
    )

    monkeypatch.setenv("PLUGIN_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("SOURCE_FILE", str(source_file))
    monkeypatch.setenv("DEMO_FILE", str(demo_file))
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "5")

    monkeypatch.setattr(app_module, "PLUGIN_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(app_module, "DEMO_MODE", True)
    monkeypatch.setattr(app_module, "PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setattr(app_module, "SOURCE_FILE", str(source_file))
    monkeypatch.setattr(app_module, "DEMO_FILE", str(demo_file))
    monkeypatch.setattr(app_module, "REQUEST_TIMEOUT_SECONDS", 5.0)

    return {
        "token": TEST_TOKEN,
        "demo_file": demo_file,
        "source_file": source_file,
        "demo_jobs": demo_jobs_payload["jobs"],
    }


@pytest.fixture
def client(configured_env) -> TestClient:
    """带鉴权配置的 TestClient。"""
    return TestClient(app_module.app)


@pytest.fixture
def auth_headers(configured_env) -> dict[str, str]:
    return {"Authorization": f"Bearer {configured_env['token']}"}
