"""猎聘授权岗位搜索编排。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from providers.liepin import cli_runner as liepin_cli_runner
from providers.liepin.cache import TtlCache
from providers.liepin.cli_runner import (
    LIEPIN_INVOCATION_MODE,
    LiepinCliError,
    run_liepin_search,
)
from providers.liepin.config import LiepinConfig, load_liepin_config
from providers.liepin.models import (
    DEFAULT_WARNINGS,
    extract_job_list,
    normalize_liepin_job,
)

logger = logging.getLogger("secskill_data_service.liepin")

_SEARCH_CACHE = TtlCache()
_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_SIZE: int | None = None
_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "liepin_snapshot.json"


def _get_semaphore(size: int) -> asyncio.Semaphore:
    global _SEMAPHORE, _SEMAPHORE_SIZE
    if _SEMAPHORE is None or _SEMAPHORE_SIZE != size:
        _SEMAPHORE = asyncio.Semaphore(size)
        _SEMAPHORE_SIZE = size
    return _SEMAPHORE


def split_liepin_keywords(
    raw: str, *, max_keywords: int
) -> tuple[list[str], bool]:
    """分隔关键词：|、英文逗号、中文逗号、换行；去空白、去重、长度 2–40。

    Returns:
        (keywords, truncated) — truncated 表示有效关键词超过 max_keywords。
    """
    parts = re.split(r"[|,\n，]+", raw or "")
    seen: set[str] = set()
    all_valid: list[str] = []
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if len(token) < 2 or len(token) > 40:
            continue
        if token in seen:
            continue
        seen.add(token)
        all_valid.append(token)
    if not all_valid:
        return ["target_job"], False
    truncated = len(all_valid) > max_keywords
    return all_valid[:max_keywords], truncated


def liepin_health_snapshot(job_provider: str = "") -> dict[str, Any]:
    """健康检查用猎聘状态（不含 Token / Token 长度 / 敏感完整路径）。"""
    cfg = load_liepin_config()
    provider = (job_provider or "").strip().lower()
    return {
        "liepin_cli_installed": liepin_cli_runner.liepin_cli_installed(
            cfg.cli_executable
        ),
        "liepin_invocation_mode": LIEPIN_INVOCATION_MODE,
        "liepin_token_configured": cfg.user_token_configured,
        "liepin_provider_enabled": provider == "liepin_cli",
        "liepin_cli_commit": cfg.cli_commit,
    }


def _cache_key(keyword: str, region: str, page: int) -> str:
    return f"{keyword}:::{region}:::{page}"


async def _search_one(
    *,
    keyword: str,
    region: str,
    page: int,
    config: LiepinConfig,
    request_id: str,
    warnings: list[str],
    strict: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """单关键词单页搜索；返回 (normalized_jobs, ledger_entry)。"""
    key = _cache_key(keyword, region, page)
    cached = _SEARCH_CACHE.get(key)
    if cached is not None:
        if "CACHE_HIT" not in warnings:
            warnings.append("CACHE_HIT")
        jobs = list(cached.get("jobs") or [])
        cached_ledger = dict(cached.get("ledger") or {})
        cached_ledger["cache_hit"] = True
        return jobs, cached_ledger

    started = datetime.now(timezone.utc)
    t0 = datetime.now(timezone.utc)
    ledger: dict[str, Any] = {
        "source_code": "liepin",
        "source_name": "猎聘",
        "status": "failed",
        "raw_count": 0,
        "accepted_count": 0,
        "duration_ms": 0,
        "error_code": None,
        "keyword": keyword,
        "page": page,
    }
    try:
        sem = _get_semaphore(config.max_concurrent)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise LiepinCliError(
                "PROVIDER_BUSY", "Liepin provider is busy"
            ) from exc
        try:
            payload = await run_liepin_search(
                keyword,
                region,
                page,
                config=config,
                request_id=request_id,
            )
        finally:
            sem.release()
        raw_jobs = extract_job_list(payload)
        normalized: list[dict[str, Any]] = []
        for raw in raw_jobs:
            item = normalize_liepin_job(raw)
            if item is not None:
                normalized.append(item)
        ledger["raw_count"] = len(raw_jobs)
        ledger["accepted_count"] = len(normalized)
        ledger["status"] = "success" if normalized else "empty"
        duration_ms = int(
            (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        )
        ledger["duration_ms"] = duration_ms
        _SEARCH_CACHE.set(
            key,
            {"jobs": normalized, "ledger": ledger},
            config.cache_ttl_seconds,
        )
        return normalized, ledger
    except LiepinCliError as exc:
        if strict:
            raise
        ledger["error_code"] = exc.error_code
        ledger["status"] = "failed"
        ledger["duration_ms"] = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        return [], ledger


def _load_snapshot_jobs() -> list[dict[str, Any]]:
    if not _SNAPSHOT_PATH.is_file():
        return []
    try:
        payload = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in jobs:
        if isinstance(raw, dict):
            item = normalize_liepin_job(raw)
            if item is not None:
                result.append(item)
    return result


async def collect_liepin_jobs(
    *,
    keywords: str,
    region: str,
    max_items: int,
    config: LiepinConfig | None = None,
    strict: bool = False,
    date_filter_unsupported: bool = False,
) -> dict[str, Any]:
    """执行猎聘只读搜索并返回统一内部结果（非 ToolResponse 包装）。

    strict=True 时，CLI/Token/Busy 等错误向上抛出，供 HTTP 层映射状态码。
    """
    cfg = config or load_liepin_config()
    request_id = uuid.uuid4().hex[:12]
    warnings: list[str] = list(DEFAULT_WARNINGS)
    if date_filter_unsupported:
        warnings.append("DATE_FILTER_NOT_SUPPORTED_BY_LIEPIN_SEARCH")
    keyword_list, truncated = split_liepin_keywords(
        keywords, max_keywords=cfg.max_keywords
    )
    if truncated:
        warnings.append("KEYWORDS_TRUNCATED")
    limit = max(1, min(int(max_items), cfg.max_items_limit))
    address = (region or "").strip()

    all_jobs: list[dict[str, Any]] = []
    ledgers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if not cfg.user_token_configured:
        if strict:
            raise LiepinCliError("TOKEN_MISSING", "Liepin token is not configured")
        warnings.append("LIEPIN_TOKEN_MISSING")
        ledgers.append(
            {
                "source_code": "liepin",
                "source_name": "猎聘",
                "status": "failed",
                "raw_count": 0,
                "accepted_count": 0,
                "duration_ms": 0,
                "error_code": "TOKEN_MISSING",
            }
        )
    else:
        if strict and not liepin_cli_runner.liepin_cli_installed(cfg.cli_executable):
            raise LiepinCliError("CLI_NOT_INSTALLED", "liepin-cli is not installed")
        # 多关键词串行；页串行（零基 page：0..(max_pages-1)）；进程内 semaphore 限制并发。
        for keyword in keyword_list:
            for page in range(cfg.max_pages):
                jobs, ledger = await _search_one(
                    keyword=keyword,
                    region=address,
                    page=page,
                    config=cfg,
                    request_id=request_id,
                    warnings=warnings,
                    strict=strict,
                )
                ledgers.append(ledger)
                for job in jobs:
                    job_id = str(job.get("job_id") or "").strip()
                    dedupe_key = job_id or f"{job.get('job_title')}|{job.get('company')}"
                    if dedupe_key in seen_ids:
                        continue
                    seen_ids.add(dedupe_key)
                    all_jobs.append(job)
                    if len(all_jobs) >= limit:
                        break
                if len(all_jobs) >= limit:
                    break
            if len(all_jobs) >= limit:
                break

    if not all_jobs and cfg.fallback_to_snapshot:
        snapshot_jobs = _load_snapshot_jobs()
        if snapshot_jobs:
            warnings.append("FALLBACK_TO_LIEPIN_SNAPSHOT")
            all_jobs = snapshot_jobs[:limit]
            ledgers.append(
                {
                    "source_code": "liepin_snapshot",
                    "source_name": "猎聘快照",
                    "status": "success",
                    "raw_count": len(snapshot_jobs),
                    "accepted_count": len(all_jobs),
                    "duration_ms": 0,
                    "error_code": None,
                }
            )
        else:
            warnings.append("LIEPIN_SNAPSHOT_NOT_FOUND")

    return {
        "count": len(all_jobs),
        "data_mode": "live_authorized_liepin",
        "provider": "liepin-cli",
        "provider_version": cfg.cli_commit,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "jobs": all_jobs[:limit],
        "source_ledger": ledgers,
        "warnings": warnings,
        "trend_claim_allowed": False,
        "skill_claim_allowed": False,
    }
