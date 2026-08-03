"""猎聘 Provider 配置（环境变量，禁止输出 Token）。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from providers.liepin.cli_pin import LIEPIN_CLI_PINNED_COMMIT

logger = logging.getLogger("secskill_data_service.liepin")


def _parse_bool(value: str | None, default: bool = False) -> bool:
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


def _parse_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    if value is None or value.strip() == "":
        result = default
    else:
        try:
            result = int(value)
        except (TypeError, ValueError):
            logger.warning("Invalid int env; using default=%s", default)
            result = default
    return max(minimum, min(maximum, result))


def _parse_float(
    value: str | None, default: float, *, minimum: float, maximum: float
) -> float:
    if value is None or value.strip() == "":
        result = default
    else:
        try:
            result = float(value)
        except (TypeError, ValueError):
            logger.warning("Invalid float env; using default=%s", default)
            result = default
    return max(minimum, min(maximum, result))


@dataclass(frozen=True)
class LiepinConfig:
    """猎聘 CLI 运行时配置。"""

    user_token_configured: bool
    timeout_seconds: float
    max_concurrent: int
    max_keywords: int
    max_pages: int
    max_items_limit: int
    cache_ttl_seconds: int
    output_max_bytes: int
    cli_commit: str
    fallback_to_snapshot: bool


def load_liepin_config() -> LiepinConfig:
    """从环境变量加载猎聘配置（不记录 Token）。"""
    token = os.getenv("LIEPIN_USER_TOKEN") or ""
    configured = bool(token.strip())
    cfg = LiepinConfig(
        user_token_configured=configured,
        timeout_seconds=_parse_float(
            os.getenv("LIEPIN_CLI_TIMEOUT_SECONDS"),
            45.0,
            minimum=5.0,
            maximum=180.0,
        ),
        max_concurrent=_parse_int(
            os.getenv("LIEPIN_MAX_CONCURRENT"), 1, minimum=1, maximum=4
        ),
        max_keywords=_parse_int(
            os.getenv("LIEPIN_MAX_KEYWORDS"), 3, minimum=1, maximum=10
        ),
        max_pages=_parse_int(os.getenv("LIEPIN_MAX_PAGES"), 1, minimum=1, maximum=5),
        max_items_limit=_parse_int(
            os.getenv("LIEPIN_MAX_ITEMS_LIMIT"), 60, minimum=1, maximum=200
        ),
        cache_ttl_seconds=_parse_int(
            os.getenv("LIEPIN_CACHE_TTL_SECONDS"), 600, minimum=0, maximum=86400
        ),
        output_max_bytes=_parse_int(
            os.getenv("LIEPIN_OUTPUT_MAX_BYTES"),
            2_000_000,
            minimum=64_000,
            maximum=8_000_000,
        ),
        cli_commit=(os.getenv("LIEPIN_CLI_COMMIT") or "").strip()
        or LIEPIN_CLI_PINNED_COMMIT,
        fallback_to_snapshot=_parse_bool(
            os.getenv("LIEPIN_FALLBACK_TO_SNAPSHOT"), default=False
        ),
    )
    logger.info(
        "liepin_config token_configured=%s timeout=%s max_concurrent=%s "
        "max_keywords=%s max_pages=%s max_items_limit=%s cache_ttl=%s "
        "output_max_bytes=%s cli_commit=%s fallback_to_snapshot=%s",
        cfg.user_token_configured,
        cfg.timeout_seconds,
        cfg.max_concurrent,
        cfg.max_keywords,
        cfg.max_pages,
        cfg.max_items_limit,
        cfg.cache_ttl_seconds,
        cfg.output_max_bytes,
        cfg.cli_commit,
        cfg.fallback_to_snapshot,
    )
    return cfg
