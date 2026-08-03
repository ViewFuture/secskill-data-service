"""猎聘 CLI 异步执行器（独立 venv 控制台脚本；仅允许 job search）。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from providers.liepin.cli_pin import DEFAULT_LIEPIN_CLI_EXECUTABLE
from providers.liepin.config import LiepinConfig
from providers.liepin.models import FORBIDDEN_CLI_TOKENS

logger = logging.getLogger("secskill_data_service.liepin")

LIEPIN_INVOCATION_MODE = "isolated_console_script"
# providers/liepin/cli_runner.py → 仓库根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LiepinCliError(Exception):
    """CLI 执行或解析失败（消息已脱敏）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def resolve_liepin_cli_executable(raw: str | None = None) -> Path:
    """解析独立猎聘 CLI 控制台脚本路径；相对路径以项目根目录为基准。"""
    text = (
        raw if raw is not None else os.getenv("LIEPIN_CLI_EXECUTABLE") or ""
    ).strip()
    if not text:
        text = DEFAULT_LIEPIN_CLI_EXECUTABLE
    path = Path(text)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


# 兼容旧名（健康检查 / 旧测试可能引用）
resolve_liepin_python_executable = resolve_liepin_cli_executable


def get_liepin_cli_executable(raw: str | None = None) -> Path:
    """解析并校验 CLI 可执行文件；失败抛出 CLI_NOT_INSTALLED。"""
    path = resolve_liepin_cli_executable(raw)
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise LiepinCliError("CLI_NOT_INSTALLED", "liepin-cli is not installed")
    except OSError as exc:
        raise LiepinCliError("CLI_NOT_INSTALLED", "liepin-cli is not installed") from exc
    return path


def liepin_cli_installed(cli_executable: str | None = None) -> bool:
    """独立 liepin-cli 控制台脚本存在且可执行。"""
    try:
        get_liepin_cli_executable(cli_executable)
        return True
    except LiepinCliError:
        return False


def build_liepin_search_argv(
    job_name: str,
    address: str,
    page: int,
    *,
    cli_executable: str | None = None,
    python_executable: str | None = None,
) -> list[str]:
    """构造只读搜索 argv：<liepin-cli> job search …"""
    # python_executable 仅为兼容旧调用签名，正式搜索忽略。
    _ = python_executable
    cli = str(resolve_liepin_cli_executable(cli_executable))
    return [
        cli,
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


def _assert_safe_search_argv(argv: list[str], *, cli_executable: Path) -> None:
    """硬校验命令列表仅包含 job search，拒绝 apply/resume/auth/skill。"""
    # 固定形态：
    # <liepin-cli> job search --job-name X --address Y --page N --output json
    if len(argv) != 11:
        raise LiepinCliError("INVALID_COMMAND", "Unexpected CLI argv shape")
    if argv[0] != str(cli_executable):
        raise LiepinCliError("INVALID_COMMAND", "CLI binary not allowed")
    if argv[1] != "job" or argv[2] != "search":
        raise LiepinCliError("FORBIDDEN_COMMAND", "Only job search is allowed")
    subcommand = " ".join(argv[1:3]).lower()
    for token in FORBIDDEN_CLI_TOKENS:
        if token in subcommand:
            raise LiepinCliError("FORBIDDEN_COMMAND", "Command not allowed")
    if argv[3] != "--job-name" or argv[5] != "--address" or argv[7] != "--page":
        raise LiepinCliError("INVALID_COMMAND", "Unexpected CLI flags")
    if argv[9] != "--output" or argv[10] != "json":
        raise LiepinCliError("INVALID_COMMAND", "Output must be json")
    joined = " ".join(argv).lower()
    if "apply" in joined or "resume" in joined or "auth" in joined:
        if argv[1] != "job" or argv[2] != "search":
            raise LiepinCliError("FORBIDDEN_COMMAND", "Command not allowed")


def sanitize_liepin_error_text(
    text: str,
    token: str,
    max_chars: int = 2000,
) -> str:
    """脱敏 stderr/错误消息尾部，供内部诊断日志使用（不进入 HTTP）。"""
    cleaned = "".join(
        ch if (ch == "\n" or ch == "\t" or ord(ch) >= 32) else " "
        for ch in (text or "")
    )
    if token:
        cleaned = cleaned.replace(token, "[REDACTED]")
    patterns = [
        r"(?i)x-user-token\s*[:=]\s*\S+",
        r"(?i)authorization\s*:\s*bearer\s+\S+",
        r"(?i)cookie\s*[:=]\s*[^\s;]+(?:;[^\s;]+)*",
        r"(?i)\btoken\s*=\s*[^&\s]+",
        r"(?i)\baccess_token\s*=\s*[^&\s]+",
        r"(?i)\buser_token\s*=\s*[^&\s]+",
        r"(?i)([?&](?:token|access_token|user_token)=)[^&\s]+",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "[REDACTED]", cleaned)
    # 岗位数组不得进入诊断日志
    cleaned = re.sub(
        r'(?is)("(?:jobs|list|items|jobList|records)"\s*:\s*)\[.*?\]',
        r"\1[REDACTED]",
        cleaned,
    )
    if len(cleaned) > max_chars:
        cleaned = cleaned[-max_chars:]
    return cleaned


def _extract_stdout_error_fields(
    stdout: bytes, token: str
) -> tuple[Any, str]:
    """从 stdout JSON 仅提取错误字段，不触碰岗位数组。"""
    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    code = payload.get("code")
    raw_msg = payload.get("message") or payload.get("msg") or payload.get("detail") or ""
    if not isinstance(raw_msg, str):
        raw_msg = str(raw_msg)
    return code, sanitize_liepin_error_text(raw_msg, token, max_chars=500)


def _log_cli_failure(
    *,
    request_id: str,
    keyword: str,
    region: str,
    page: int,
    duration_ms: int,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    token: str,
) -> None:
    stdout_code, stdout_msg = _extract_stdout_error_fields(stdout, token)
    stderr_tail = sanitize_liepin_error_text(
        stderr.decode("utf-8", errors="replace"),
        token,
        max_chars=2000,
    )
    logger.info(
        "event=liepin_cli_failed request_id=%s keyword=%s region=%s page=%s "
        "duration_ms=%s returncode=%s stdout_bytes=%s stderr_bytes=%s "
        "stderr_tail_sanitized=%s stdout_error_code=%s "
        "stdout_error_message_sanitized=%s",
        request_id,
        keyword,
        region,
        page,
        duration_ms,
        returncode,
        len(stdout),
        len(stderr),
        stderr_tail,
        stdout_code,
        stdout_msg,
    )


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
    """异步执行独立 venv 中的 `liepin-cli job search`。

    Token 仅通过子进程环境变量 LIEPIN_USER_TOKEN 传递，不写入 argv。
    """
    rid = request_id or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    error_code: str | None = None
    item_count = 0
    returncode: int | None = None
    stdout = b""
    stderr = b""
    token = ""

    token = (os.getenv("LIEPIN_USER_TOKEN") or "").strip()
    if not token or not config.user_token_configured:
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

    try:
        cli_path = get_liepin_cli_executable(config.cli_executable)
    except LiepinCliError as exc:
        error_code = exc.error_code
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
        raise

    argv = build_liepin_search_argv(
        job_name,
        address,
        page,
        cli_executable=str(cli_path),
    )
    _assert_safe_search_argv(argv, cli_executable=cli_path)

    # 继承完整环境，并显式写入 Token；不在日志中打印 env。
    env = os.environ.copy()
    env["LIEPIN_USER_TOKEN"] = token
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
            out = await _read_limited(proc.stdout, config.output_max_bytes)
            err = await _read_limited(
                proc.stderr, min(config.output_max_bytes, 256_000)
            )
            await proc.wait()
            return out, err

        try:
            stdout, stderr = await asyncio.wait_for(
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
            _log_cli_failure(
                request_id=rid,
                keyword=job_name,
                region=address,
                page=page,
                duration_ms=int((time.perf_counter() - started) * 1000),
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                token=token,
            )
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
