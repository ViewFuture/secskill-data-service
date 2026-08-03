"""对已启动的 SecSkill_Data_Service 做端到端冒烟测试。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(override=False)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
REQUIRED_INNER_KEYS = (
    "count",
    "data_mode",
    "batch_preview",
    "raw_items",
    "source_ledger",
    "warnings",
)


def _fail(message: str, code: int = 1) -> None:
    print(f"smoke_test failed: {message}", file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    base_url = (os.getenv("SERVICE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    plugin_token = os.getenv("PLUGIN_TOKEN")
    if not plugin_token:
        _fail("PLUGIN_TOKEN is not set")

    health_url = f"{base_url}/health"
    collect_url = f"{base_url}/plugin/v1/jobs/collect"
    payload = {
        "keywords": "网络安全运维 安全运营 SOC",
        "region": "广东",
        "start_date": "2026-05-01",
        "end_date": "2026-08-01",
        "max_items": 20,
    }

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            health = client.get(health_url)
            if health.status_code != 200:
                _fail(f"GET /health expected 200, got {health.status_code}")

            collect = client.post(
                collect_url,
                headers={"Authorization": f"Bearer {plugin_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        _fail(f"HTTP request error: {type(exc).__name__}")

    if collect.status_code != 200:
        _fail(f"POST /plugin/v1/jobs/collect expected 200, got {collect.status_code}")

    try:
        outer: dict[str, Any] = collect.json()
    except ValueError:
        _fail("collect response is not valid JSON")

    if "result" not in outer:
        _fail("outer response missing result")
    if not isinstance(outer["result"], str):
        _fail(f"outer result must be string, got {type(outer['result']).__name__}")

    try:
        inner = json.loads(outer["result"])
    except json.JSONDecodeError as exc:
        _fail(f"result is not valid JSON string: {exc.msg}")

    if not isinstance(inner, dict):
        _fail("parsed result must be an object")

    missing = [key for key in REQUIRED_INNER_KEYS if key not in inner]
    if missing:
        _fail(f"inner result missing fields: {', '.join(missing)}")

    # 输出摘要；绝不打印 PLUGIN_TOKEN
    print("service_url:", base_url)
    print("http_status:", collect.status_code)
    print("data_mode:", inner.get("data_mode"))
    print("count:", inner.get("count"))
    ledger = inner.get("source_ledger")
    print("source_ledger_count:", len(ledger) if isinstance(ledger, list) else "n/a")
    print("warnings:", inner.get("warnings"))


if __name__ == "__main__":
    main()
