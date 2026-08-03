"""DEMO_MODE 下岗位采集接口测试。"""

from __future__ import annotations

import json
from collections import Counter

COLLECT_PATH = "/plugin/v1/jobs/collect"


def _collect(client, auth_headers, **overrides):
    body = {
        "keywords": "安全",
        "region": "",
        "start_date": "2026-05-01",
        "end_date": "2026-08-01",
        "max_items": 20,
    }
    body.update(overrides)
    return client.post(COLLECT_PATH, headers=auth_headers, json=body)


def _parse_result(response) -> dict:
    payload = response.json()
    assert "result" in payload, payload
    assert isinstance(payload["result"], str), type(payload["result"])
    return json.loads(payload["result"])


def test_collect_returns_200(client, auth_headers):
    response = _collect(client, auth_headers)
    assert response.status_code == 200, response.text


def test_collect_outer_contains_result_string(client, auth_headers):
    response = _collect(client, auth_headers)
    payload = response.json()
    assert "result" in payload, payload
    assert isinstance(payload["result"], str), payload
    # 外层应只有 result，或至少包含 result
    assert set(payload.keys()) == {"result"} or "result" in payload


def test_collect_result_is_json_string_with_required_keys(client, auth_headers):
    response = _collect(client, auth_headers)
    inner = _parse_result(response)
    for key in (
        "count",
        "data_mode",
        "batch_preview",
        "raw_items",
        "source_ledger",
        "warnings",
    ):
        assert key in inner, f"缺少字段 {key}: {inner.keys()}"


def test_collect_data_mode_is_demo(client, auth_headers):
    inner = _parse_result(_collect(client, auth_headers))
    assert inner["data_mode"] == "demo", inner


def test_collect_warnings_include_demo_flag(client, auth_headers):
    inner = _parse_result(_collect(client, auth_headers))
    assert "DEMO_DATA_NOT_FOR_REAL_TREND_CLAIMS" in inner["warnings"], inner["warnings"]


def test_collect_keyword_filter_works(client, auth_headers):
    inner = _parse_result(_collect(client, auth_headers, keywords="SOC分析师"))
    titles = [item["job_title"] for item in inner["raw_items"]]
    assert titles, "关键词 SOC分析师 应至少命中1条"
    assert all("SOC" in title or "SOC" in str(item) for title, item in zip(titles, inner["raw_items"]))
    assert all(
        "SOC" in (
            item["job_title"]
            + item["company"]
            + item["description"]
            + " ".join(item.get("skills") or [])
        )
        for item in inner["raw_items"]
    ), inner["raw_items"]


def test_collect_region_filter_works(client, auth_headers):
    inner = _parse_result(
        _collect(client, auth_headers, keywords="工程师", region="广东佛山")
    )
    assert inner["raw_items"], "地区过滤后应有结果"
    assert all(item["region"] == "广东佛山" for item in inner["raw_items"]), inner[
        "raw_items"
    ]


def test_collect_date_range_filter_works(client, auth_headers):
    # 仅覆盖 2026-06-01，应命中 SOC，排除 05-10 运维与 07-20 运营，以及范围外应急响应
    inner = _parse_result(
        _collect(
            client,
            auth_headers,
            keywords="分析师 运营 运维 应急",
            start_date="2026-06-01",
            end_date="2026-06-01",
        )
    )
    dates = {item["publish_date"] for item in inner["raw_items"]}
    assert dates == {"2026-06-01"}, dates


def test_collect_max_items_is_respected(client, auth_headers):
    inner = _parse_result(
        _collect(client, auth_headers, keywords="工程师 分析师 运营", max_items=1)
    )
    assert inner["count"] == 1, inner
    assert len(inner["raw_items"]) == 1, inner["raw_items"]


def test_collect_start_after_end_returns_400(client, auth_headers):
    response = _collect(
        client,
        auth_headers,
        start_date="2026-08-01",
        end_date="2026-05-01",
    )
    assert response.status_code == 400, response.text


def test_collect_invalid_date_format_returns_400(client, auth_headers):
    response = _collect(
        client,
        auth_headers,
        start_date="2026/05/01",
        end_date="2026-08-01",
    )
    assert response.status_code == 400, response.text


def test_collect_max_items_zero_returns_422(client, auth_headers):
    response = _collect(client, auth_headers, max_items=0)
    assert response.status_code == 422, response.text


def test_collect_max_items_201_returns_422(client, auth_headers):
    response = _collect(client, auth_headers, max_items=201)
    assert response.status_code == 422, response.text


def test_collect_deduplicates_duplicate_jobs(client, auth_headers):
    inner = _parse_result(
        _collect(
            client,
            auth_headers,
            keywords="网络安全运维工程师",
            region="广东佛山",
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
    )
    keys = [
        (
            item["job_title"],
            item["company"],
            item["region"],
            item["publish_date"],
        )
        for item in inner["raw_items"]
    ]
    counts = Counter(keys)
    assert keys, "应采集到运维岗位"
    assert all(count == 1 for count in counts.values()), counts


def test_collect_filters_invalid_publish_date_records(client, auth_headers):
    inner = _parse_result(
        _collect(
            client,
            auth_headers,
            keywords="渗透测试",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )
    assert all(item["publish_date"] != "2026-99-99" for item in inner["raw_items"])
    assert all(
        "非法日期" not in item.get("description", "") for item in inner["raw_items"]
    )
    # 临时 fixtures 中渗透测试仅非法日期一条，过滤后应为空
    assert inner["count"] == 0, inner
