"""导出并校验 OpenAPI Schema 为 secskill-data-service.openapi.json。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402

OUT_FILE = ROOT / "secskill-data-service.openapi.json"
COLLECT_PATH = "/plugin/v1/jobs/collect"
REQUIRED_REQUEST_FIELDS = (
    "keywords",
    "region",
    "start_date",
    "end_date",
    "max_items",
)


def _fail(message: str) -> None:
    print(f"OpenAPI validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _resolve_ref(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """解析本地 $ref（仅支持 #/components/...）。"""
    ref = node.get("$ref")
    if not ref:
        return node
    if not isinstance(ref, str) or not ref.startswith("#/"):
        _fail(f"Unsupported $ref: {ref}")
    cur: Any = schema
    for part in ref[2:].split("/"):
        if not isinstance(cur, dict) or part not in cur:
            _fail(f"Unable to resolve $ref: {ref}")
        cur = cur[part]
    if not isinstance(cur, dict):
        _fail(f"$ref does not point to an object: {ref}")
    return cur


def _find_bearer_scheme(schema: dict[str, Any]) -> bool:
    schemes = schema.get("components", {}).get("securitySchemes", {})
    if not isinstance(schemes, dict):
        return False
    for spec in schemes.values():
        if not isinstance(spec, dict):
            continue
        if spec.get("type") == "http" and str(spec.get("scheme", "")).lower() == "bearer":
            return True
    return False


def validate_openapi(schema: dict[str, Any]) -> None:
    """校验导出前必须满足的星辰插件约定。"""
    title = schema.get("info", {}).get("title")
    if title != "SecSkill_Data_Service":
        _fail(f"info.title must be SecSkill_Data_Service, got {title!r}")

    paths = schema.get("paths")
    if not isinstance(paths, dict) or COLLECT_PATH not in paths:
        _fail(f"paths must include {COLLECT_PATH}")

    post = paths[COLLECT_PATH].get("post")
    if not isinstance(post, dict):
        _fail(f"{COLLECT_PATH} must define POST")

    operation_id = post.get("operationId")
    if operation_id != "collectPublicJobs":
        _fail(f"operationId must be collectPublicJobs, got {operation_id!r}")

    request_body = post.get("requestBody")
    if not isinstance(request_body, dict):
        _fail("POST requestBody is missing")
    content = request_body.get("content", {})
    app_json = content.get("application/json", {}) if isinstance(content, dict) else {}
    body_schema = app_json.get("schema") if isinstance(app_json, dict) else None
    if not isinstance(body_schema, dict):
        _fail("requestBody application/json schema is missing")
    body_schema = _resolve_ref(schema, body_schema)
    properties = body_schema.get("properties", {})
    if not isinstance(properties, dict):
        _fail("request schema properties missing")
    missing = [name for name in REQUIRED_REQUEST_FIELDS if name not in properties]
    if missing:
        _fail(f"request body missing fields: {', '.join(missing)}")

    responses = post.get("responses", {})
    ok = responses.get("200") if isinstance(responses, dict) else None
    if not isinstance(ok, dict):
        _fail("POST 200 response is missing")
    ok_content = ok.get("content", {})
    ok_json = ok_content.get("application/json", {}) if isinstance(ok_content, dict) else {}
    ok_schema = ok_json.get("schema") if isinstance(ok_json, dict) else None
    if not isinstance(ok_schema, dict):
        _fail("200 response application/json schema is missing")
    ok_schema = _resolve_ref(schema, ok_schema)
    result_schema = ok_schema.get("properties", {}).get("result")
    if not isinstance(result_schema, dict):
        _fail("200 response must include properties.result")
    result_schema = _resolve_ref(schema, result_schema)
    if result_schema.get("type") != "string":
        _fail(f"200 response result.type must be string, got {result_schema.get('type')!r}")

    if not _find_bearer_scheme(schema):
        # 也允许 operation 级 security 引用 HTTPBearer
        security = post.get("security") or schema.get("security") or []
        has_bearer_ref = False
        schemes = schema.get("components", {}).get("securitySchemes", {})
        if isinstance(security, list) and isinstance(schemes, dict):
            for item in security:
                if not isinstance(item, dict):
                    continue
                for name in item:
                    spec = schemes.get(name, {})
                    if (
                        isinstance(spec, dict)
                        and spec.get("type") == "http"
                        and str(spec.get("scheme", "")).lower() == "bearer"
                    ):
                        has_bearer_ref = True
        if not has_bearer_ref:
            _fail("OpenAPI must include HTTP Bearer security scheme")


def main() -> None:
    schema = app.openapi()
    if not isinstance(schema, dict):
        _fail("app.openapi() did not return a dict")
    validate_openapi(schema)
    OUT_FILE.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(str(OUT_FILE))


if __name__ == "__main__":
    main()
