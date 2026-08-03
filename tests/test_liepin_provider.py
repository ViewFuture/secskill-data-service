"""猎聘 Provider / collectLiepinJobs 完整测试（禁止真实调用猎聘）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import app as app_module
import providers.liepin.provider as liepin_provider
from providers.liepin.cache import TtlCache
from providers.liepin.cli_runner import (
    LiepinCliError,
    build_liepin_search_argv,
    liepin_cli_installed,
    run_liepin_search,
)
from providers.liepin.config import LiepinConfig
from providers.liepin.models import DEFAULT_WARNINGS, normalize_liepin_job
from providers.liepin.provider import collect_liepin_jobs, split_liepin_keywords

COLLECT_LIEPIN = "/plugin/v1/jobs/collect-liepin"
BODY = {
    "keywords": "网络安全工程师|信息安全工程师",
    "region": "广东",
    "max_items": 20,
    "start_date": "",
    "end_date": "",
}

REQUIRED_WARNINGS = list(DEFAULT_WARNINGS)


def _cfg(**overrides: Any) -> LiepinConfig:
    base = dict(
        user_token_configured=True,
        timeout_seconds=5.0,
        max_concurrent=1,
        max_keywords=3,
        max_pages=1,
        max_items_limit=60,
        cache_ttl_seconds=600,
        output_max_bytes=2_000_000,
        cli_commit="test-commit",
        fallback_to_snapshot=False,
        python_executable=".liepin-venv/bin/python",
    )
    base.update(overrides)
    return LiepinConfig(**base)


def _job(job_id: str, title: str = "网络安全工程师", company: str = "演示企业A") -> dict:
    return {
        "jobId": job_id,
        "jobType": "全职",
        "jobName": title,
        "company": company,
        "location": "广东深圳",
        "salary": "20-30k",
        "education": "本科",
        "workYears": "3-5年",
        "industry": "互联网",
        "companyTags": ["安全"],
        "financingStage": "B轮",
        "companySize": "500-999",
        "jobDetailUrl": f"https://example.org/{job_id}",
    }


def _ok_payload(jobs: list[dict]) -> dict:
    return {"code": 0, "data": {"jobs": jobs}}


def _parse(resp) -> dict:
    outer = resp.json()
    assert isinstance(outer["result"], str), type(outer["result"])
    return json.loads(outer["result"])


class _FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        if n < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.returncode: int | None = None if hang else returncode
        self._final_code = returncode
        self._hang = hang
        self.killed = False

    async def wait(self) -> int:
        if self._hang and not self.killed:
            await asyncio.sleep(3600)
        self.returncode = self._final_code
        return self._final_code

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._hang = False


@pytest.fixture(autouse=True)
def _clear_liepin_runtime_state():
    liepin_provider._SEARCH_CACHE.clear()
    liepin_provider._SEMAPHORE = None
    liepin_provider._SEMAPHORE_SIZE = None
    yield
    liepin_provider._SEARCH_CACHE.clear()
    liepin_provider._SEMAPHORE = None
    liepin_provider._SEMAPHORE_SIZE = None


# ---------------------------------------------------------------------------
# 关键词 / 映射
# ---------------------------------------------------------------------------


def test_build_liepin_search_argv_uses_module_invocation(tmp_path):
    fake_py = tmp_path / "liepin-python"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_py.chmod(0o755)

    argv = build_liepin_search_argv(
        "安全工程师",
        "广东",
        1,
        python_executable=str(fake_py),
    )
    assert argv[0] == str(fake_py.resolve())
    assert argv[1:5] == ["-m", "liepin_cli.main", "job", "search"]
    assert "--job-name" in argv and "安全工程师" in argv
    assert "apply" not in argv and "resume" not in argv and "auth" not in argv
    assert liepin_cli_installed(str(fake_py)) is True
    missing = tmp_path / "missing-python"
    assert liepin_cli_installed(str(missing)) is False


def test_liepin_python_without_execute_permission(tmp_path):
    fake_py = tmp_path / "noexec-python"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_py.chmod(0o644)
    assert liepin_cli_installed(str(fake_py)) is False


def test_split_keywords_separators_and_dedupe():
    keys, truncated = split_liepin_keywords(
        "安全,运维，SOC|安全\n渗透", max_keywords=10
    )
    assert keys == ["安全", "运维", "SOC", "渗透"]
    assert truncated is False


def test_split_keywords_truncates_and_flags():
    keys, truncated = split_liepin_keywords(
        "安全工程师|运维工程师|SOC分析师|渗透测试工程师",
        max_keywords=3,
    )
    assert keys == ["安全工程师", "运维工程师", "SOC分析师"]
    assert truncated is True


def test_split_keywords_default_target_job():
    keys, truncated = split_liepin_keywords(" ", max_keywords=3)
    assert keys == ["target_job"]
    assert truncated is False


def test_normalize_liepin_job_fields():
    item = normalize_liepin_job(_job("J1"))
    assert item is not None
    assert item["job_id"] == "J1"
    assert item["job_title"] == "网络安全工程师"
    assert item["publish_date"] is None
    assert item["trend_eligible"] is False


# ---------------------------------------------------------------------------
# CLI runner（fake subprocess）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_success_two_jobs():
    payload = _ok_payload([_job("1"), _job("2", title="信息安全工程师")])
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=raw, returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            result = await run_liepin_search("安全", "广东", 1, config=_cfg())
    assert result["code"] == 0
    assert len(result["data"]["jobs"]) == 2


@pytest.mark.asyncio
async def test_cli_nonzero_returncode():
    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=b"{}", returncode=2)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            with pytest.raises(LiepinCliError) as exc:
                await run_liepin_search("安全", "广东", 1, config=_cfg())
    assert exc.value.error_code == "CLI_NONZERO_EXIT"


@pytest.mark.asyncio
async def test_cli_stdout_not_json():
    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=b"not-json{{{", returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            with pytest.raises(LiepinCliError) as exc:
                await run_liepin_search("安全", "广东", 1, config=_cfg())
    assert exc.value.error_code == "INVALID_JSON"


@pytest.mark.asyncio
async def test_cli_payload_code_nonzero():
    raw = json.dumps({"code": 1001, "data": {"jobs": []}}).encode()

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=raw, returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            with pytest.raises(LiepinCliError) as exc:
                await run_liepin_search("安全", "广东", 1, config=_cfg())
    assert exc.value.error_code == "PAYLOAD_CODE_ERROR"


@pytest.mark.asyncio
async def test_cli_stdout_exceeds_max_bytes():
    huge = b"x" * 5000

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=huge, returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            with pytest.raises(LiepinCliError) as exc:
                await run_liepin_search(
                    "安全",
                    "广东",
                    1,
                    config=_cfg(output_max_bytes=1024),
                )
    assert exc.value.error_code == "OUTPUT_TOO_LARGE"


@pytest.mark.asyncio
async def test_cli_timeout_kills_process():
    proc = _FakeProcess(hang=True)

    async def _fake_exec(*args, **kwargs):
        return proc

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ):
            with pytest.raises(LiepinCliError) as exc:
                await run_liepin_search(
                    "安全",
                    "广东",
                    1,
                    config=_cfg(timeout_seconds=0.05),
                )
    assert exc.value.error_code == "TIMEOUT"
    assert proc.killed is True


@pytest.mark.asyncio
async def test_cli_token_missing():
    with pytest.raises(LiepinCliError) as exc:
        await run_liepin_search(
            "安全", "广东", 1, config=_cfg(user_token_configured=False)
        )
    assert exc.value.error_code == "TOKEN_MISSING"


@pytest.mark.asyncio
async def test_cli_not_installed():
    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=False):
        with pytest.raises(LiepinCliError) as exc:
            await run_liepin_search("安全", "广东", 1, config=_cfg())
    assert exc.value.error_code == "CLI_NOT_INSTALLED"


# ---------------------------------------------------------------------------
# Provider 编排
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_two_jobs_success_and_fixed_warnings():
    payload = _ok_payload(
        [_job("1"), _job("2", title="信息安全工程师", company="演示企业B")]
    )
    with patch(
        "providers.liepin.provider.run_liepin_search",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        result = await collect_liepin_jobs(
            keywords="网络安全工程师",
            region="广东",
            max_items=20,
            config=_cfg(cache_ttl_seconds=0),
        )
    assert result["count"] == 2
    assert result["data_mode"] == "live_authorized_liepin"
    assert all(item["trend_eligible"] is False for item in result["jobs"])
    for w in REQUIRED_WARNINGS:
        assert w in result["warnings"]


@pytest.mark.asyncio
async def test_provider_dedupe_by_job_id():
    payload = _ok_payload([_job("DUP"), _job("DUP"), _job("OTHER", title="渗透测试")])
    with patch(
        "providers.liepin.provider.run_liepin_search",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        result = await collect_liepin_jobs(
            keywords="安全",
            region="广东",
            max_items=20,
            config=_cfg(cache_ttl_seconds=0),
        )
    assert result["count"] == 2
    ids = [j["job_id"] for j in result["jobs"]]
    assert ids.count("DUP") == 1


@pytest.mark.asyncio
async def test_provider_keywords_truncated_warning():
    calls: list[str] = []

    async def _fake(job_name, address, page, config, request_id=None):
        calls.append(job_name)
        return _ok_payload([_job(job_name)])

    with patch(
        "providers.liepin.provider.run_liepin_search",
        side_effect=_fake,
    ):
        result = await collect_liepin_jobs(
            keywords="安全工程师|运维工程师|SOC分析师|渗透测试工程师",
            region="广东",
            max_items=20,
            config=_cfg(max_keywords=3, cache_ttl_seconds=0),
        )
    assert "KEYWORDS_TRUNCATED" in result["warnings"]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_provider_max_items_limit():
    jobs = [_job(str(i), title=f"岗位{i}", company=f"企业{i}") for i in range(10)]
    with patch(
        "providers.liepin.provider.run_liepin_search",
        new_callable=AsyncMock,
        return_value=_ok_payload(jobs),
    ):
        result = await collect_liepin_jobs(
            keywords="安全",
            region="广东",
            max_items=3,
            config=_cfg(cache_ttl_seconds=0),
        )
    assert result["count"] == 3
    assert len(result["jobs"]) == 3


@pytest.mark.asyncio
async def test_provider_cache_hit():
    mock_search = AsyncMock(
        return_value=_ok_payload([_job("C1")])
    )
    cfg = _cfg(cache_ttl_seconds=600)
    with patch("providers.liepin.provider.run_liepin_search", mock_search):
        first = await collect_liepin_jobs(
            keywords="缓存测试", region="广东", max_items=10, config=cfg
        )
        second = await collect_liepin_jobs(
            keywords="缓存测试", region="广东", max_items=10, config=cfg
        )
    assert mock_search.await_count == 1
    assert "CACHE_HIT" in second["warnings"]
    assert first["count"] == second["count"] == 1


# ---------------------------------------------------------------------------
# HTTP 接口
# ---------------------------------------------------------------------------


def test_endpoint_success_integration(client, auth_headers, tmp_path):
    """端到端：mock create_subprocess_exec，走真实 endpoint + provider + runner。"""
    payload = _ok_payload(
        [_job("H1"), _job("H2", title="信息安全工程师", company="演示企业B")]
    )
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    fake_py = tmp_path / "liepin-python"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_py.chmod(0o755)
    resolved = fake_py.resolve()

    async def _fake_exec(*args, **kwargs):
        assert args[0] == str(resolved)
        assert args[1:5] == ("-m", "liepin_cli.main", "job", "search")
        assert "apply" not in args
        assert "resume" not in args
        assert "auth" not in args
        return _FakeProcess(stdout=raw, returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(
                cache_ttl_seconds=0,
                python_executable=str(fake_py),
            ),
        ):
            with patch(
                "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                resp = client.post(COLLECT_LIEPIN, headers=auth_headers, json=BODY)
    assert resp.status_code == 200
    assert isinstance(resp.json()["result"], str)
    inner = _parse(resp)
    assert inner["data_mode"] == "live_authorized_liepin"
    assert inner["count"] == 2
    assert all(j["trend_eligible"] is False for j in inner["jobs"])
    for w in REQUIRED_WARNINGS:
        assert w in inner["warnings"]


def test_endpoint_date_filter_warning(client, auth_headers):
    payload = _ok_payload([_job("D1")])
    raw = json.dumps(payload).encode()

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(stdout=raw, returncode=0)

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(cache_ttl_seconds=0),
        ):
            with patch(
                "providers.liepin.cli_runner.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                resp = client.post(
                    COLLECT_LIEPIN,
                    headers=auth_headers,
                    json={
                        **BODY,
                        "start_date": "2026-05-01",
                        "end_date": "2026-08-01",
                    },
                )
    assert resp.status_code == 200
    inner = _parse(resp)
    assert "DATE_FILTER_NOT_SUPPORTED_BY_LIEPIN_SEARCH" in inner["warnings"]


def test_endpoint_wrong_bearer_returns_401(client):
    resp = client.post(
        COLLECT_LIEPIN,
        headers={"Authorization": "Bearer wrong-token"},
        json=BODY,
    )
    assert resp.status_code == 401


def test_endpoint_token_missing_503(client, auth_headers):
    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(user_token_configured=False),
        ):
            resp = client.post(COLLECT_LIEPIN, headers=auth_headers, json=BODY)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "LIEPIN_TOKEN_NOT_CONFIGURED"


def test_endpoint_cli_missing_503(client, auth_headers):
    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=False):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(),
        ):
            resp = client.post(COLLECT_LIEPIN, headers=auth_headers, json=BODY)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "LIEPIN_CLI_NOT_INSTALLED"


def test_endpoint_timeout_504(client, auth_headers):
    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(cache_ttl_seconds=0),
        ):
            with patch(
                "providers.liepin.provider.run_liepin_search",
                new_callable=AsyncMock,
                side_effect=LiepinCliError("TIMEOUT", "timed out"),
            ):
                resp = client.post(COLLECT_LIEPIN, headers=auth_headers, json=BODY)
    assert resp.status_code == 504
    assert resp.json()["detail"]["error_code"] == "LIEPIN_CLI_TIMEOUT"


def test_endpoint_busy_429(client, auth_headers):
    async def _busy_wait_for(coro, *args, **kwargs):
        # 关闭未 await 的 acquire 协程，避免 RuntimeWarning
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.TimeoutError()

    with patch("providers.liepin.cli_runner.liepin_cli_installed", return_value=True):
        with patch(
            "providers.liepin.config.load_liepin_config",
            return_value=_cfg(max_concurrent=1, cache_ttl_seconds=0),
        ):
            with patch(
                "providers.liepin.provider.asyncio.wait_for",
                side_effect=_busy_wait_for,
            ):
                resp = client.post(COLLECT_LIEPIN, headers=auth_headers, json=BODY)
    assert resp.status_code == 429
    assert resp.json()["detail"]["error_code"] == "LIEPIN_PROVIDER_BUSY"


def test_no_apply_or_resume_routes(client):
    schema = app_module.app.openapi()
    paths = " ".join(schema["paths"].keys()).lower()
    assert "apply" not in paths
    assert "resume" not in paths
    for path, methods in schema["paths"].items():
        blob = json.dumps(methods, ensure_ascii=False).lower()
        assert "job apply" not in blob
        assert "resume update" not in blob
        assert "resume add" not in blob
    # 仅允许只读采集路径
    assert "/plugin/v1/jobs/collect-liepin" in schema["paths"]
    assert (
        schema["paths"]["/plugin/v1/jobs/collect-liepin"]["post"]["operationId"]
        == "collectLiepinJobs"
    )


def test_openapi_result_is_string(client):
    schema = app_module.app.openapi()
    liepin = schema["paths"]["/plugin/v1/jobs/collect-liepin"]["post"]
    assert liepin["summary"] == "猎聘授权岗位需求采集"
    ref = liepin["responses"]["200"]["content"]["application/json"]["schema"]
    name = ref["$ref"].split("/")[-1]
    assert schema["components"]["schemas"][name]["properties"]["result"]["type"] == "string"


def test_ttl_cache_unit():
    cache = TtlCache()
    cache.set("a", 1, 600)
    assert cache.get("a") == 1
    cache.set("b", 2, 0)
    assert cache.get("b") is None


def test_liepin_cli_isolation_pin_is_consistent(monkeypatch):
    """主 requirements 不含 CLI；独立 requirements-liepin 钉死完整 SHA。"""
    from pathlib import Path

    from providers.liepin.cli_pin import (
        LIEPIN_CLI_PINNED_COMMIT,
        LIEPIN_CLI_VERSION_NOT_PINNED,
    )
    from providers.liepin.config import load_liepin_config
    from providers.liepin.provider import liepin_health_snapshot

    root = Path(__file__).resolve().parents[1]
    main_req = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "liepin-cli" not in main_req
    assert "liepin-cil" not in main_req
    assert "git+https://github.com/liepin-tech-2026/liepin-cil" not in main_req
    liepin_req = (root / "requirements-liepin.txt").read_text(encoding="utf-8").strip()
    assert liepin_req == (
        "git+https://github.com/liepin-tech-2026/liepin-cil.git@"
        f"{LIEPIN_CLI_PINNED_COMMIT}"
    )
    assert len(LIEPIN_CLI_PINNED_COMMIT) == 40
    assert LIEPIN_CLI_VERSION_NOT_PINNED is False
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    assert "LIEPIN_PYTHON_EXECUTABLE=.liepin-venv/bin/python" in env_example
    assert f"LIEPIN_CLI_COMMIT={LIEPIN_CLI_PINNED_COMMIT}" in env_example
    render = (root / "render.yaml").read_text(encoding="utf-8")
    assert "bash build.sh" in render
    assert "LIEPIN_PYTHON_EXECUTABLE" in render
    assert LIEPIN_CLI_PINNED_COMMIT in render
    assert "WEB_CONCURRENCY" in render
    assert (root / ".python-version").read_text(encoding="utf-8").strip() == "3.12.7"
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".liepin-venv/" in gitignore
    monkeypatch.delenv("LIEPIN_CLI_COMMIT", raising=False)
    monkeypatch.delenv("LIEPIN_PYTHON_EXECUTABLE", raising=False)
    cfg = load_liepin_config()
    assert cfg.cli_commit == LIEPIN_CLI_PINNED_COMMIT
    assert cfg.python_executable == ".liepin-venv/bin/python"
    health = liepin_health_snapshot("mcp_jobs")
    assert health["liepin_invocation_mode"] == "isolated_python_module"
    assert isinstance(health["liepin_token_configured"], bool)
    assert isinstance(health["liepin_cli_installed"], bool)
    assert "liepin_provider_enabled" in health
    dumped = json.dumps(health)
    assert "LIEPIN_USER_TOKEN" not in dumped
    assert "Bearer" not in dumped
