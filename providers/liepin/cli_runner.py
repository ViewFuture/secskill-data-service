"""猎聘 CLI 异步执行器（仅允许 job search）。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

from providers.liepin.config import LiepinConfig
from providers.liepin.models import FORBIDDEN_CLI_TOKENS

logger = logging.getLogger("secskill_data_service.liepin")

LIEPIN_MODULE = "liepin_cli.main"


class LiepinCliError(Exception):
    """CLI 执行或解析失败（消息已脱敏）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def liepin_cli_installed() -> bool:
    """检查 liepin_cli 包是否可导入（以模块方式调用，不依赖 PATH 入口脚本）。"""
    return importlib.util.find_spec("liepin_cli") is not None


def build_liepin_search_argv(job_name: str, address: str, page: int) -> list[str]:
    """构造只读搜索 argv：sys.executable -m liepin_cli.main job search …"""
    return [
        sys.executable,
        "-m",
        LIEPIN_MODULE,
        "job",
        "search",
        "--job-name",
        job_name,
        "--address",
        address,
        "--page",
        str(page),
        "--output",
        "json",
    ]


def _assert_safe_search_argv(argv: list[str]) -> None:
    """硬校验命令列表仅包含 job search，拒绝 apply/resume/auth/skill 子命令。"""
    # 固定形态：
    # <python> -m liepin_cli.main job search --job-name X --address Y --page N --output json
    if len(argv) != 13:
        raise LiepinCliError("INVALID_COMMAND", "Unexpected CLI argv shape")
    if argv[0] != sys.executable:
        raise LiepinCliError("INVALID_COMMAND", "CLI interpreter not allowed")
    if argv[1] != "-m" or argv[2] != LIEPIN_MODULE:
        raise LiepinCliError("INVALID_COMMAND", "CLI module not allowed")
    if argv[3] != "job" or argv[4] != "search":
        raise LiepinCliError("FORBIDDEN_COMMAND", "Only job search is allowed")
    subcommand = " ".join(argv[3:5]).lower()
    for token in FORBIDDEN_CLI_TOKENS:
        if token in subcommand:
            raise LiepinCliError("FORBIDDEN_COMMAND", "Command not allowed")
    if argv[5] != "--job-name" or argv[7] != "--address" or argv[9] != "--page":
        raise LiepinCliError("INVALID_COMMAND", "Unexpected CLI flags")
    if argv[11] != "--output" or argv[12] != "json":
        raise LiepinCliError("INVALID_COMMAND", "Output must be json")


async def _read_limited(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await stream.read(64 * 1024)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise LiepinCliError("OUTPUT_TOO_LARGE", "CLI stdout exceeds size limit")
        chunks.append(block)
    return b"".join(chunks)


async def run_liepin_search(
    job_name: str,
    address: str,
    page: int,
    *,
    config: LiepinConfig,
    request_id: str | None = None,
) -> dict[str, Any]:
    """异步执行 `python -m liepin_cli.main job search`，返回解析后的 JSON 对象。

    Token 仅通过继承环境变量 LIEPIN_USER_TOKEN 传递，不写入 argv。
    """
    rid = request_id or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    error_code: str | None = None
    item_count = 0
    returncode: int | None = None

    if not config.user_token_configured:
        error_code = "TOKEN_MISSING"
        logger.info(
            "liepin_search request_id=%s keyword=%s region=%s page=%s "
            "duration_ms=%s returncode=%s item_count=%s error_code=%s",
            rid,
            job_name,
            address,
            page,
            int((time.perf_counter() - started) * 1000),
            returncode,
            item_count,
            error_code,
        )
        raise LiepinCliError(error_code, "Liepin token is not configured")

    if not liepin_cli_installed():
        error_code = "CLI_NOT_INSTALLED"
        logger.info(
            "liepin_search request_id=%s keyword=%s region=%s page=%s "
            "duration_ms=%s returncode=%s item_count=%s error_code=%s",
            rid,
            job_name,
            address,
            page,
            int((time.perf_counter() - started) * 1000),
            returncode,
            item_count,
            error_code,
        )
        raise LiepinCliError(error_code, "liepin-cli is not installed")

    argv = build_liepin_search_argv(job_name, address, page)
    _assert_safe_search_argv(argv)

    # 继承当前环境（含 LIEPIN_USER_TOKEN）；不在日志中打印 env。
    env = os.environ.copy()
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        async def _communicate() -> tuple[bytes, bytes]:
            assert proc is not None
            assert proc.stdout is not None
            assert proc.stderr is not None
            stdout = await _read_limited(proc.stdout, config.output_max_bytes)
            # stderr 同样限制读取，但不回传内容
            stderr = await _read_limited(
                proc.stderr, min(config.output_max_bytes, 256_000)
            )
            await proc.wait()
            return stdout, stderr

        try:
            stdout, _stderr = await asyncio.wait_for(
                _communicate(),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            error_code = "TIMEOUT"
            if proc.returncode is None:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            raise LiepinCliError(error_code, "Liepin CLI timed out") from exc

        returncode = proc.returncode
        if returncode != 0:
            error_code = "CLI_NONZERO_EXIT"
            raise LiepinCliError(error_code, "Liepin CLI exited with error")

        try:
            text = stdout.decode("utf-8")
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            error_code = "INVALID_JSON"
            raise LiepinCliError(
                error_code, "Liepin CLI stdout is not valid JSON"
            ) from exc

        if not isinstance(payload, dict):
            error_code = "INVALID_PAYLOAD"
            raise LiepinCliError(error_code, "Liepin CLI payload must be an object")

        code = payload.get("code")
        if code != 0 and code != "0":
            error_code = "PAYLOAD_CODE_ERROR"
            raise LiepinCliError(error_code, "Liepin CLI payload.code is not 0")

        # 不返回简历/个人信息；调用方只取岗位列表。
        data = payload.get("data")
        if isinstance(data, dict):
            for sensitive in ("resume", "resumes", "personal", "profile", "userInfo"):
                data.pop(sensitive, None)
        item_count = 0
        if isinstance(data, list):
            item_count = len(data)
        elif isinstance(data, dict):
            for key in ("jobs", "list", "items", "records", "jobList"):
                value = data.get(key)
                if isinstance(value, list):
                    item_count = len(value)
                    break

        return payload
    except LiepinCliError:
        raise
    except Exception as exc:  # noqa: BLE001
        error_code = type(exc).__name__
        raise LiepinCliError("CLI_EXEC_ERROR", "Liepin CLI execution failed") from exc
    finally:
        logger.info(
            "liepin_search request_id=%s keyword=%s region=%s page=%s "
            "duration_ms=%s returncode=%s item_count=%s error_code=%s",
            rid,
            job_name,
            address,
            page,
            int((time.perf_counter() - started) * 1000),
            returncode,
            item_count,
            error_code,
        )
