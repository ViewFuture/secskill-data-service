"""SecSkill_Data_Service — 岗安智练公开岗位数据采集 FastAPI 服务。"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import socket
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

# Render / 系统环境变量优先；本地 .env 仅在未设置时补充。
load_dotenv(override=False)

logger = logging.getLogger("secskill_data_service")
logging.basicConfig(level=logging.INFO)

ROOT_DIR = Path(__file__).resolve().parent
USER_AGENT = "SecSkill-Agent/1.0"
DEMO_WARNING = "DEMO_DATA_NOT_FOR_REAL_TREND_CLAIMS"
NO_SOURCES_WARNING = "NO_ENABLED_PUBLIC_SOURCES"
MAX_FEED_BYTES = 1_048_576
MAX_REDIRECTS = 3
MAX_DESCRIPTION_CHARS = 2_000
MAX_SKILL_ITEMS = 32
MAX_SKILL_LEN = 64
MAX_TEXT_FIELD_LEN = 200


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """统一布尔解析：true/1/yes/on → True；false/0/no/off/空串 → False。

    禁止使用 bool("false")（恒为 True）。忽略大小写与前后空白。
    """
    if value is None:
        return default
    text = value.strip().lower()
    if text == "":
        return False
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_float(value: str | None, default: float = 15.0) -> float:
    """安全地将环境变量转换为 float。"""
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid float env value; using default=%s", default)
        return default


def _parse_int(value: str | None, default: int = 1) -> int:
    """安全地将环境变量转换为 int。"""
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid int env value; using default=%s", default)
        return default


PLUGIN_TOKEN: str | None = os.getenv("PLUGIN_TOKEN") or None
PUBLIC_BASE_URL: str = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
DEMO_MODE: bool = _parse_bool(os.getenv("DEMO_MODE"), default=True)
SOURCE_FILE: str = os.getenv("SOURCE_FILE") or "sources.json"
DEMO_FILE: str = os.getenv("DEMO_FILE") or "fixtures/jobs.json"
REQUEST_TIMEOUT_SECONDS: float = _parse_float(
    os.getenv("REQUEST_TIMEOUT_SECONDS"), default=15.0
)
JOB_PROVIDER: str = (os.getenv("JOB_PROVIDER") or "").strip().lower()
MCP_JOBS_ENABLED: bool = _parse_bool(os.getenv("MCP_JOBS_ENABLED"), default=False)
MCP_JOBS_BASE_URL: str = (os.getenv("MCP_JOBS_BASE_URL") or "").rstrip("/")
MCP_JOBS_TOKEN: str | None = os.getenv("MCP_JOBS_TOKEN") or None
MCP_JOBS_TIMEOUT_SECONDS: float = _parse_float(
    os.getenv("MCP_JOBS_TIMEOUT_SECONDS"), default=180.0
)
MCP_JOBS_FALLBACK_TO_DEMO: bool = _parse_bool(
    os.getenv("MCP_JOBS_FALLBACK_TO_DEMO"), default=True
)
MCP_JOBS_PAGE: int = _parse_int(os.getenv("MCP_JOBS_PAGE"), default=1)
DATE_FILTER_MODE: str = (os.getenv("DATE_FILTER_MODE") or "soft").strip().lower()

logger.info(
    "runtime_config demo_mode=%s job_provider=%s mcp_jobs_enabled=%s "
    "adapter_base_url_configured=%s adapter_token_configured=%s "
    "mcp_jobs_timeout_seconds=%s mcp_jobs_page=%s",
    DEMO_MODE,
    JOB_PROVIDER or "(empty)",
    MCP_JOBS_ENABLED,
    bool(MCP_JOBS_BASE_URL),
    bool(MCP_JOBS_TOKEN),
    MCP_JOBS_TIMEOUT_SECONDS,
    MCP_JOBS_PAGE,
)

_servers = [{"url": PUBLIC_BASE_URL}] if PUBLIC_BASE_URL else None

app = FastAPI(
    title="SecSkill_Data_Service",
    description="为「岗安智练 SecSkill Agent」提供公开岗位数据采集接口。",
    version="0.1.0",
    servers=_servers,
)

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CollectPublicJobsRequest(BaseModel):
    """公开岗位采集请求体。"""

    keywords: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="检索关键词，支持逗号/空格分隔",
    )
    region: str = Field(
        default="",
        max_length=100,
        description="地区筛选，可为空",
    )
    start_date: str = Field(
        ...,
        min_length=10,
        max_length=10,
        description="开始日期 YYYY-MM-DD（含）",
    )
    end_date: str = Field(
        ...,
        min_length=10,
        max_length=10,
        description="结束日期 YYYY-MM-DD（含）",
    )
    max_items: int = Field(default=20, ge=1, le=200, description="最大返回条数")


class ToolResponse(BaseModel):
    """星辰 Agent 自定义插件约定响应：result 为 JSON 字符串。"""

    result: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def require_plugin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """校验 Authorization: Bearer <PLUGIN_TOKEN>。"""
    if not PLUGIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: plugin token is not set",
        )
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, PLUGIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_json_file(path: str | Path) -> Any:
    """读取并解析 JSON 文件。

    Args:
        path: 相对项目根目录或绝对路径。

    Returns:
        解析后的 JSON 对象。

    Raises:
        FileNotFoundError: 文件不存在。
        json.JSONDecodeError: JSON 非法。
    """
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = ROOT_DIR / file_path
    with file_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_sources(source_file: str | None = None) -> list[dict[str, Any]]:
    """加载公开数据源白名单列表。

    Args:
        source_file: 白名单文件路径，默认使用 SOURCE_FILE。

    Returns:
        sources 数组；结构异常时返回空列表。
    """
    path = source_file or SOURCE_FILE
    try:
        payload = load_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load sources file: %s", type(exc).__name__)
        return []
    if isinstance(payload, dict):
        sources = payload.get("sources", [])
        return sources if isinstance(sources, list) else []
    if isinstance(payload, list):
        return payload
    return []


def load_demo_items(demo_file: str | None = None) -> list[dict[str, Any]]:
    """加载本地演示岗位数据。

    Args:
        demo_file: 演示数据文件路径，默认使用 DEMO_FILE。

    Returns:
        岗位字典列表。
    """
    path = demo_file or DEMO_FILE
    try:
        payload = load_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load demo file: %s", type(exc).__name__)
        return []
    if isinstance(payload, dict):
        jobs = payload.get("jobs", [])
        return jobs if isinstance(jobs, list) else []
    if isinstance(payload, list):
        return payload
    return []


def parse_date(value: str | None) -> date | None:
    """将 YYYY-MM-DD（或以其为前缀的时间戳）解析为 date；失败返回 None。

    Args:
        value: 日期字符串。

    Returns:
        date 或 None。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # 优先截取前 10 位 YYYY-MM-DD（兼容 "2026-07-01T12:00:00"）
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    # 兼容 ISO / 空格分隔等常见时间格式；绝不使用当前时间回填
    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        return None


PUBLISH_DATE_CANDIDATE_KEYS: tuple[str, ...] = (
    "publish_date",
    "publish_time",
    "publish_time_raw",
    "posted_at",
    "date",
)


def extract_publish_date_raw(raw: dict[str, Any]) -> str:
    """按优先级提取原始发布日期字段，不读取 collected_at。"""
    for key in PUBLISH_DATE_CANDIDATE_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_publish_date(raw_value: str) -> str:
    """将原始日期规范化为 YYYY-MM-DD；无法解析则返回空串，绝不伪造。"""
    parsed = parse_date(raw_value)
    if parsed is None:
        return ""
    return parsed.isoformat()


def _truncate(text: str, limit: int) -> str:
    """截断过长文本以限制响应体规模。"""
    if len(text) <= limit:
        return text
    return text[:limit]


def normalize_skills(skills: Any) -> list[str]:
    """将 skills 字段规范化为字符串列表。

    Args:
        skills: 列表、逗号分隔字符串或其他类型。

    Returns:
        去空白后的技能字符串列表。
    """
    if skills is None:
        return []
    if isinstance(skills, list):
        values = [str(item).strip() for item in skills if str(item).strip()]
    elif isinstance(skills, str):
        parts = re.split(r"[,，;/|]+", skills)
        values = [part.strip() for part in parts if part.strip()]
    else:
        values = [str(skills).strip()] if str(skills).strip() else []
    return [_truncate(item, MAX_SKILL_LEN) for item in values[:MAX_SKILL_ITEMS]]


def normalize_item(
    raw: dict[str, Any],
    *,
    source_code: str,
    source_name: str,
) -> dict[str, Any] | None:
    """将原始岗位记录规范化为统一结构。

    Args:
        raw: 原始岗位字典。
        source_code: 数据源编码。
        source_name: 数据源名称。

    Returns:
        规范化后的岗位字典；缺少关键字段时返回 None。
    """
    if not isinstance(raw, dict):
        return None
    job_title = _truncate(
        str(raw.get("job_title") or raw.get("title") or "").strip(),
        MAX_TEXT_FIELD_LEN,
    )
    company = _truncate(
        str(raw.get("company") or raw.get("employer") or "").strip(),
        MAX_TEXT_FIELD_LEN,
    )
    if not job_title or not company:
        return None
    region = _truncate(
        str(raw.get("region") or raw.get("city") or raw.get("location") or "").strip(),
        MAX_TEXT_FIELD_LEN,
    )
    publish_date_raw = _truncate(extract_publish_date_raw(raw), 64)
    publish_date = normalize_publish_date(publish_date_raw)
    description = _truncate(
        str(raw.get("description") or raw.get("desc") or "").strip(),
        MAX_DESCRIPTION_CHARS,
    )
    skills = normalize_skills(raw.get("skills"))
    source_url = _truncate(
        str(raw.get("source_url") or raw.get("url") or "").strip(),
        500,
    )
    return {
        "job_title": job_title,
        "company": company,
        "region": region,
        "publish_date": publish_date,
        "publish_date_raw": publish_date_raw,
        "trend_eligible": bool(publish_date),
        "description": description,
        "skills": skills,
        "source_url": source_url,
        "source_code": source_code,
        "source_name": source_name,
    }


def _split_keywords(keywords: str) -> list[str]:
    """按中文逗号、英文逗号和空格拆分关键词。"""
    parts = re.split(r"[,，\s]+", keywords.strip())
    return [part for part in parts if part]


def matches_request(
    item: dict[str, Any],
    *,
    keywords: list[str],
    region: str,
    start: date,
    end: date,
) -> bool:
    """判断规范化岗位是否匹配采集请求条件。

    Args:
        item: 规范化岗位。
        keywords: 已拆分关键词列表（任意命中）。
        region: 地区包含匹配；空字符串表示不限。
        start: 发布日起始（含）。
        end: 发布日结束（含）。

    Returns:
        是否匹配。日期无法解析时视为不匹配。
    """
    publish = parse_date(item.get("publish_date"))
    if publish is None:
        return False
    if publish < start or publish > end:
        return False

    if region.strip():
        item_region = str(item.get("region") or "")
        if region.strip() not in item_region:
            return False

    if not keywords:
        return True

    skills_text = " ".join(normalize_skills(item.get("skills")))
    haystack = " ".join(
        [
            str(item.get("job_title") or ""),
            str(item.get("company") or ""),
            str(item.get("description") or ""),
            skills_text,
        ]
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按岗位名称、公司、地区、发布日期联合去重（保留首次出现）。

    Args:
        items: 岗位列表。

    Returns:
        去重后的列表。
    """
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (
            str(item.get("job_title") or "").strip(),
            str(item.get("company") or "").strip(),
            str(item.get("region") or "").strip(),
            str(item.get("publish_date") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def validate_public_https_url(url: str) -> str:
    """校验 URL 仅为可公开访问的 HTTPS，阻止内网/回环等 SSRF 目标。

    Args:
        url: 待校验 URL。

    Returns:
        校验通过的原始 URL。

    Raises:
        ValueError: URL 不安全或协议/主机非法。
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL userinfo is not allowed")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL hostname is required")
    host_lower = hostname.lower().rstrip(".")
    if host_lower == "localhost" or host_lower.endswith(".localhost"):
        raise ValueError("localhost is not allowed")

    addresses: list[str] = []
    try:
        ip = ipaddress.ip_address(hostname)
        addresses = [str(ip)]
    except ValueError:
        try:
            addr_infos = socket.getaddrinfo(hostname, None)
            addresses = sorted({info[4][0] for info in addr_infos})
        except OSError as exc:
            raise ValueError("Unable to resolve hostname") from exc

    if not addresses:
        raise ValueError("Unable to resolve hostname")

    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("Resolved address is not a public IP")

    return url


def _extract_jobs_payload(payload: Any) -> list[dict[str, Any]]:
    """从 JSON Feed 载荷中提取岗位字典列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("jobs", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("JSON feed does not contain a recognizable job list")


async def _read_response_limited(response: httpx.Response, limit: int) -> bytes:
    """读取响应正文，超过字节上限则失败。"""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise ValueError("Feed response too large")
        except ValueError as exc:
            if str(exc) == "Feed response too large":
                raise
            # 非法 Content-Length 时继续按流式上限读取
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ValueError("Feed response too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_json_feed(
    url: str,
    *,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """通过 HTTPS 拉取公开 JSON Feed 并提取岗位列表。

    手动跟随有限次重定向，并对每一跳重新执行公网 HTTPS 校验，降低 SSRF 风险。

    Args:
        url: 已通过白名单与安全校验的 HTTPS URL。
        timeout: 请求超时秒数，默认 REQUEST_TIMEOUT_SECONDS。

    Returns:
        原始岗位字典列表。

    Raises:
        ValueError / httpx.HTTPError: 拉取或解析失败。
    """
    current_url = validate_public_https_url(url)
    request_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_SECONDS

    async with httpx.AsyncClient(
        timeout=request_timeout,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect without Location header")
                    next_url = urljoin(str(response.url), location)
                    current_url = validate_public_https_url(next_url)
                    continue

                response.raise_for_status()
                raw = await _read_response_limited(response, MAX_FEED_BYTES)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("Feed response is not valid JSON") from exc
                return _extract_jobs_payload(payload)

    raise ValueError("Too many redirects")


def _source_feed_url(source: dict[str, Any]) -> str | None:
    """从白名单条目提取 feed URL。"""
    for key in ("url", "feed_url", "base_url"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_tool_result(
    *,
    items: list[dict[str, Any]],
    data_mode: str,
    request: CollectPublicJobsRequest,
    source_ledger: list[dict[str, Any]],
    warnings: list[str],
    provider: str | None = None,
    provider_version: str | None = None,
) -> ToolResponse:
    """组装星辰插件约定的 ToolResponse。"""
    payload: dict[str, Any] = {
        "count": len(items),
        "data_mode": data_mode,
        "provider": provider,
        "provider_version": provider_version,
        "batch_preview": {
            "keywords": request.keywords,
            "region": request.region,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "max_items": request.max_items,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        },
        "raw_items": items,
        "source_ledger": source_ledger,
        "warnings": warnings,
    }
    return ToolResponse(result=json.dumps(payload, ensure_ascii=False))


def _validate_request_dates(request: CollectPublicJobsRequest) -> tuple[date, date]:
    """校验请求日期并返回 date 区间。"""
    start = parse_date(request.start_date)
    end = parse_date(request.end_date)
    if start is None or end is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date and end_date must be YYYY-MM-DD",
        )
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must not be later than end_date",
        )
    return start, end


def _mcp_search_url() -> str:
    """构造 Adapter 搜索 URL（BASE 去尾斜杠后拼接一次路径）。"""
    base = MCP_JOBS_BASE_URL.rstrip("/")
    return f"{base}/internal/v1/jobs/search"


def _map_adapter_jobs(
    jobs: list[Any],
    *,
    provider_code: str,
    provider_name: str,
) -> list[dict[str, Any]]:
    """将 Adapter jobs 规范化为网关 raw_items。"""
    items: list[dict[str, Any]] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        item = normalize_item(
            raw,
            source_code=str(raw.get("source_code") or provider_code),
            source_name=str(raw.get("source_name") or provider_name),
        )
        if item is None:
            # 保留 Adapter 原始可用字段，避免因字段别名丢失整条记录
            title = str(raw.get("job_title") or raw.get("title") or "").strip()
            company = str(raw.get("company") or "").strip()
            if not title:
                continue
            publish_date_raw = extract_publish_date_raw(raw)
            publish_date = normalize_publish_date(publish_date_raw)
            item = {
                "job_title": title,
                "company": company or "unknown",
                "region": str(raw.get("region") or raw.get("city") or ""),
                "publish_date": publish_date,
                "publish_date_raw": publish_date_raw,
                "trend_eligible": bool(publish_date),
                "description": str(raw.get("description") or ""),
                "skills": normalize_skills(raw.get("skills")),
                "source_url": str(raw.get("source_url") or raw.get("url") or ""),
                "source_code": provider_code,
                "source_name": provider_name,
            }
        items.append(item)
    return items


def _apply_mcp_date_filter(
    items: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    mode: str,
) -> tuple[list[dict[str, Any]], int]:
    """按 DATE_FILTER_MODE 处理 MCP 岗位日期。

    soft:
      - 可解析日期：执行区间过滤；
      - 缺失日期：保留岗位，trend_eligible=false，不伪造日期。
    hard:
      - 缺失或越界日期：丢弃。
    """
    missing = 0
    kept: list[dict[str, Any]] = []
    soft = mode != "hard"
    for item in items:
        publish_date = str(item.get("publish_date") or "").strip()
        parsed = parse_date(publish_date)
        if parsed is None:
            missing += 1
            item["publish_date"] = ""
            item["trend_eligible"] = False
            # 禁止用 collected_at / 当前时间回填
            if soft:
                kept.append(item)
            continue
        item["trend_eligible"] = True
        if parsed < start or parsed > end:
            continue
        kept.append(item)
    return kept, missing


async def call_mcp_jobs_adapter(
    request: CollectPublicJobsRequest,
) -> tuple[dict[str, Any], int]:
    """调用 MCP Jobs Adapter，返回 (JSON body, http_status)。

    不记录 Token / Authorization / 完整请求头。
    """
    if not MCP_JOBS_BASE_URL:
        raise ValueError("MCP_JOBS_BASE_URL is not configured")
    if not MCP_JOBS_TOKEN:
        raise ValueError("MCP_JOBS_TOKEN is not configured")

    url = _mcp_search_url()
    payload = {
        "keyword": request.keywords,
        "city": request.region,
        "page": MCP_JOBS_PAGE,
        "max_items": request.max_items,
        "salary": "",
        "work_year": "",
    }
    headers = {
        "X-Internal-Token": MCP_JOBS_TOKEN,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(
        timeout=MCP_JOBS_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = await client.post(url, headers=headers, json=payload)
        status_code = response.status_code
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Adapter response must be a JSON object")
        return body, status_code


async def _collect_mcp_jobs(
    request: CollectPublicJobsRequest,
    *,
    keywords: list[str],
    start: date,
    end: date,
) -> ToolResponse:
    """通过 MCP Jobs Adapter 采集岗位。"""
    selected_provider = "mcp_jobs"
    try:
        body, http_status = await call_mcp_jobs_adapter(request)
        adapter_mode = str(body.get("mode") or "").strip().lower()
        provider = str(body.get("provider") or "mcp-jobs")
        provider_version = body.get("provider_version")
        if provider_version is not None:
            provider_version = str(provider_version)
        jobs_value = body.get("jobs")
        jobs_raw: list[Any] = jobs_value if isinstance(jobs_value, list) else []
        ledger_value = body.get("source_ledger")
        ledger: list[dict[str, Any]] = (
            [item for item in ledger_value if isinstance(item, dict)]
            if isinstance(ledger_value, list)
            else []
        )
        warnings_value = body.get("warnings")
        warnings: list[str] = (
            [str(item) for item in warnings_value]
            if isinstance(warnings_value, list)
            else []
        )

        items = _map_adapter_jobs(
            jobs_raw,
            provider_code="mcp_jobs",
            provider_name=provider,
        )
        items, missing_dates = _apply_mcp_date_filter(
            items,
            start=start,
            end=end,
            mode=DATE_FILTER_MODE,
        )
        if missing_dates:
            warnings.append(f"MISSING_PUBLISH_DATE_COUNT:{missing_dates}")
        items = deduplicate_items(items)[: request.max_items]

        if adapter_mode == "live":
            data_mode = "live_public_mcp"
        else:
            data_mode = "mcp_adapter_fixture"

        logger.info(
            "collect_done selected_provider=%s adapter_response_mode=%s "
            "adapter_http_status=%s returned_job_count=%s",
            selected_provider,
            adapter_mode or "(empty)",
            http_status,
            len(items),
        )
        return _build_tool_result(
            items=items,
            data_mode=data_mode,
            request=request,
            source_ledger=ledger,
            warnings=warnings,
            provider=provider,
            provider_version=provider_version,
        )
    except Exception as exc:  # noqa: BLE001 — Adapter 失败不静默落入 public sources
        err_name = type(exc).__name__
        logger.warning(
            "collect_done selected_provider=%s adapter_response_mode=error "
            "adapter_http_status=none returned_job_count=0 error_type=%s",
            selected_provider,
            err_name,
        )
        if MCP_JOBS_FALLBACK_TO_DEMO:
            fallback = await _collect_demo(
                request, keywords=keywords, start=start, end=end
            )
            inner = json.loads(fallback.result)
            warnings = list(inner.get("warnings") or [])
            warnings.append("FALLBACK_TO_DEMO_AFTER_MCP_FAILURE")
            warnings.append(f"MCP_ADAPTER_ERROR:{err_name}")
            return _build_tool_result(
                items=list(inner.get("raw_items") or []),
                data_mode="fallback_demo",
                request=request,
                source_ledger=list(inner.get("source_ledger") or []),
                warnings=warnings,
                provider=None,
                provider_version=None,
            )
        return _build_tool_result(
            items=[],
            data_mode="live_public_mcp_failed",
            request=request,
            source_ledger=[],
            warnings=[
                "MCP_JOBS_ADAPTER_FAILED",
                f"MCP_ADAPTER_ERROR:{err_name}",
            ],
            provider="mcp-jobs",
            provider_version=None,
        )


async def _collect_demo(
    request: CollectPublicJobsRequest,
    *,
    keywords: list[str],
    start: date,
    end: date,
) -> ToolResponse:
    """演示模式采集。"""
    warnings = [DEMO_WARNING]
    source_ledger: list[dict[str, Any]] = []
    raw_jobs = load_demo_items()
    normalized: list[dict[str, Any]] = []
    for raw in raw_jobs:
        item = normalize_item(
            raw,
            source_code="demo_fixture",
            source_name="本地演示数据",
        )
        if item is None:
            continue
        if matches_request(
            item,
            keywords=keywords,
            region=request.region,
            start=start,
            end=end,
        ):
            normalized.append(item)

    deduped = deduplicate_items(normalized)[: request.max_items]
    source_ledger.append(
        {
            "source_code": "demo_fixture",
            "source_name": "本地演示数据",
            "status": "ok",
            "fetched": len(raw_jobs),
            "matched": len(deduped),
        }
    )
    return _build_tool_result(
        items=deduped,
        data_mode="demo",
        request=request,
        source_ledger=source_ledger,
        warnings=warnings,
    )


async def _collect_live(
    request: CollectPublicJobsRequest,
    *,
    keywords: list[str],
    start: date,
    end: date,
) -> ToolResponse:
    """公开数据源实时采集（仅白名单 json_feed）。"""
    warnings: list[str] = []
    source_ledger: list[dict[str, Any]] = []
    collected: list[dict[str, Any]] = []

    enabled_sources = [
        src
        for src in load_sources()
        if isinstance(src, dict)
        and bool(src.get("enabled"))
        and str(src.get("type") or "").strip() == "json_feed"
    ]
    if not enabled_sources:
        warnings.append(NO_SOURCES_WARNING)

    for source in enabled_sources:
        source_code = str(source.get("id") or source.get("code") or "unknown")
        source_name = str(source.get("name") or source_code)
        feed_url = _source_feed_url(source)
        ledger_entry: dict[str, Any] = {
            "source_code": source_code,
            "source_name": source_name,
            "status": "error",
            "fetched": 0,
            "matched": 0,
        }
        if not feed_url:
            ledger_entry["error"] = "Missing feed URL in source whitelist"
            warnings.append(f"SOURCE_SKIPPED:{source_code}:missing_url")
            source_ledger.append(ledger_entry)
            continue
        try:
            raw_jobs = await fetch_json_feed(feed_url)
            matched_count = 0
            for raw in raw_jobs:
                item = normalize_item(
                    raw,
                    source_code=source_code,
                    source_name=source_name,
                )
                if item is None:
                    continue
                if matches_request(
                    item,
                    keywords=keywords,
                    region=request.region,
                    start=start,
                    end=end,
                ):
                    collected.append(item)
                    matched_count += 1
            ledger_entry.update(
                {
                    "status": "ok",
                    "fetched": len(raw_jobs),
                    "matched": matched_count,
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 — 单源失败不中断全局
            # 不记录可能含敏感信息的完整异常细节到响应；仅类型名。
            err_name = type(exc).__name__
            logger.warning("Source %s failed: %s", source_code, err_name)
            ledger_entry["error"] = f"Fetch failed: {err_name}"
            warnings.append(f"SOURCE_FAILED:{source_code}:{err_name}")
        source_ledger.append(ledger_entry)

    deduped = deduplicate_items(collected)[: request.max_items]
    return _build_tool_result(
        items=deduped,
        data_mode="live_public",
        request=request,
        source_ledger=source_ledger,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root() -> dict[str, str]:
    """服务根信息。"""
    return {
        "service": "SecSkill_Data_Service",
        "status": "running",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {
        "status": "ok",
        "service": "SecSkill_Data_Service",
    }


@app.post(
    "/plugin/v1/jobs/collect",
    response_model=ToolResponse,
    operation_id="collectPublicJobs",
    dependencies=[Depends(require_plugin_token)],
)
async def collect_public_jobs(request: CollectPublicJobsRequest) -> ToolResponse:
    """采集公开岗位数据（demo / MCP Jobs Adapter / 白名单 JSON Feed）。"""
    start, end = _validate_request_dates(request)
    keywords = _split_keywords(request.keywords)
    if not keywords:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="keywords must contain at least one non-empty token",
        )

    # 优先级：DEMO_MODE → mcp_jobs → public sources
    if DEMO_MODE:
        logger.info(
            "collect_routing selected_provider=demo demo_mode=true job_provider=%s",
            JOB_PROVIDER or "(empty)",
        )
        result = await _collect_demo(
            request, keywords=keywords, start=start, end=end
        )
        logger.info(
            "collect_done selected_provider=demo adapter_response_mode=none "
            "adapter_http_status=none returned_job_count=%s",
            json.loads(result.result).get("count"),
        )
        return result

    if JOB_PROVIDER == "mcp_jobs" and MCP_JOBS_ENABLED:
        return await _collect_mcp_jobs(
            request, keywords=keywords, start=start, end=end
        )

    logger.info(
        "collect_routing selected_provider=public_sources demo_mode=false "
        "job_provider=%s mcp_jobs_enabled=%s",
        JOB_PROVIDER or "(empty)",
        MCP_JOBS_ENABLED,
    )
    result = await _collect_live(
        request, keywords=keywords, start=start, end=end
    )
    logger.info(
        "collect_done selected_provider=public_sources adapter_response_mode=none "
        "adapter_http_status=none returned_job_count=%s",
        json.loads(result.result).get("count"),
    )
    return result
