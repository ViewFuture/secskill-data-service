# 猎聘 liepin-cli 集成测试报告（R6）

- **执行时间**：2026-08-03 19:57 +0800
- **工作目录**：`/home/test/Projects/working/match/secskill-data-service`
- **已推送 HEAD**：`94f950306f85d3092225aaf93f1226f939bcf9d8`
- **说明**：测试使用 fake subprocess / mock，**禁止真实调用猎聘接口**；本报告不自动 push

---

## 1. 环境

| 项 | 值 |
|----|-----|
| 审计机 Python | `3.12.7`（Anaconda） |
| 仓库 `.python-version` | `3.12.7`（P0-1 已修复） |
| 钉死 liepin-cli commit | `858a62bd839d490e8745b7503961e4676a54b9d7`（P0-2 已修复） |
| 本机 `liepin-cli` | PATH 中存在，但测试已 mock，不依赖真实上游 |
| 依赖 | `requirements.txt`（fastapi/uvicorn/httpx/pytest/pytest-asyncio 等） |

---

## 2. 执行命令与结果

```bash
cd /home/test/Projects/working/match/secskill-data-service
python -m pytest -q
ruff check app.py providers tests
mypy app.py providers
```

| 命令 | 结果 |
|------|------|
| `python -m pytest -q` | **78 passed**（约 0.17s） |
| `ruff check app.py providers tests` | **All checks passed!** |
| `mypy app.py providers` | **Success: no issues found in 8 source files** |

按文件子集复核（含原接口与猎聘）：

```bash
python -m pytest tests/test_collect_jobs.py tests/test_auth.py tests/test_health.py \
  tests/test_mcp_provider.py tests/test_security.py tests/test_liepin_provider.py -q
```

| 文件 | 角色 |
|------|------|
| `tests/test_liepin_provider.py` | 猎聘 Provider + `collectLiepinJobs`（**27** 条） |
| `tests/test_collect_jobs.py` 等 | 原 `collectPublicJobs` / 鉴权 / 健康 / MCP / SSRF |

**结论：当前工作区全部测试通过。**

---

## 3. 猎聘用例覆盖映射（需求 ↔ 测试）

| # | 需求 | 测试（代表） | 状态 |
|---|------|--------------|------|
| 1 | CLI 已安装，code=0，返回 2 条岗位 | `test_cli_success_two_jobs` / `test_provider_two_jobs_success_and_fixed_warnings` / `test_endpoint_success_integration` | 通过 |
| 2 | jobId 重复去重 | `test_provider_dedupe_by_job_id` | 通过 |
| 3 | 多关键词分隔与去重 | `test_split_keywords_separators_and_dedupe` | 通过 |
| 4 | 超过最大关键词数截断并告警 | `test_split_keywords_truncates_and_flags` / `test_provider_keywords_truncated_warning`（`KEYWORDS_TRUNCATED`） | 通过 |
| 5 | max_items 限制 | `test_provider_max_items_limit` | 通过 |
| 6 | returncode 非 0 | `test_cli_nonzero_returncode` | 通过 |
| 7 | stdout 非 JSON | `test_cli_stdout_not_json` | 通过 |
| 8 | payload.code 非 0 | `test_cli_payload_code_nonzero` | 通过 |
| 9 | stdout 超过最大字节数 | `test_cli_stdout_exceeds_max_bytes` | 通过 |
| 10 | CLI 超时且子进程被 kill | `test_cli_timeout_kills_process` | 通过 |
| 11 | Token 未配置 | `test_cli_token_missing` / `test_endpoint_token_missing_503` | 通过 |
| 12 | CLI 不存在 | `test_cli_not_installed` / `test_endpoint_cli_missing_503` | 通过 |
| 13 | CACHE_HIT | `test_provider_cache_hit` | 通过 |
| 14 | Semaphore 占用 → 429 | `test_endpoint_busy_429` | 通过 |
| 15 | Bearer Token 正确 | `test_endpoint_success_integration` | 通过 |
| 16 | Bearer 错误 → 401 | `test_endpoint_wrong_bearer_returns_401` | 通过 |
| 17 | result 必须是 String | endpoint 断言 + `test_openapi_result_is_string` | 通过 |
| 18 | 内层 `data_mode=live_authorized_liepin` | provider/endpoint 断言 | 通过 |
| 19 | 内层 `trend_eligible` 全 false | provider/endpoint 断言 | 通过 |
| 20 | 固定 warnings 四项 | `REQUIRED_WARNINGS` / `DEFAULT_WARNINGS` | 通过 |
| 21 | 有 start/end_date → `DATE_FILTER_NOT_SUPPORTED_BY_LIEPIN_SEARCH` | `test_endpoint_date_filter_warning` | 通过 |
| 22 | 无可调用 apply/resume 写路由 | `test_no_apply_or_resume_routes` | 通过 |

模拟手段：`_FakeProcess` / `_FakeStream`、`patch(asyncio.create_subprocess_exec)`、`AsyncMock(run_liepin_search)`、`patch(wait_for)`（429）。

---

## 4. 原接口回归摘要

| 区域 | 结果 |
|------|------|
| `collectPublicJobs` demo / 过滤 / 去重 | 通过（既有用例） |
| Bearer 鉴权 401/500 | 通过 |
| MCP Adapter 路由与日期映射 | 通过 |
| SSRF / 超大 Feed / 重定向 | 通过 |
| `/health` | 通过（现含猎聘布尔字段，无 Token） |

原接口路径与 `operationId=collectPublicJobs` 仍存在于 OpenAPI。

---

## 5. 质量门禁

| 门禁 | 状态 |
|------|------|
| 单元/接口测试 | **通过**（78/78） |
| Lint（ruff） | **通过** |
| 类型检查（mypy） | **通过** |
| 真实猎聘 E2E | **未跑**（按安全要求禁止） |
| Render 生产冒烟 | **未跑**（需部署后按 `DEPLOY_RENDER_LIEPIN_R6.md`） |

---

## 6. 已知测试缺口 / 残留风险

1. **未验证真实 `liepin-cli@858a62bd…` 输出 schema**：若上游字段更名，映射可能静默变空（自动化仍 mock 子进程）。
2. **429 用例通过 mock `wait_for`**：未做多请求真实占满信号量的集成压测。
3. **多 worker / 多实例缓存与并发**：无测试覆盖。
4. **嵌套误克隆目录** `secskill-data-service/`：已 gitignore，不参与本次 pytest（`pytest.ini` `testpaths=tests`）。
5. **Pin 一致性**：`test_liepin_cli_commit_pin_is_consistent` 覆盖 cli_pin / requirements-liepin / env / render / `.python-version`。

---

## 7. 结论

- **功能与安全向自动化测试：通过。**
- **可合并测试门禁：通过（在当前工作区）。**
- **P0 已解除**；对真实猎聘放量前仍需：Dashboard 配置 Token + 按 `DEPLOY_RENDER_LIEPIN_R6.md` 公网冒烟。

相关文档：

- `SECURITY_AUDIT_LIEPIN_R6.md`
- `DEPLOY_RENDER_LIEPIN_R6.md`
