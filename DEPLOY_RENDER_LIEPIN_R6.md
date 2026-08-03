# Render 部署手册：猎聘 liepin-cli 集成（R6）

- **文档日期**：2026-08-03
- **配套审计**：`SECURITY_AUDIT_LIEPIN_R6.md`
- **配套测试**：`TEST_REPORT_LIEPIN_R6.md`
- **说明**：只指导部署与配置；**不自动 push**；勿将真实 Token 写入仓库

---

## 0. 发布前门禁（必须先过）

审计 R6 结论原为**有条件通过**。P0 已在本轮落地：

| 门禁 | 要求 | 当前状态 |
|------|------|----------|
| Python | 固定 **3.12.7**（`.python-version` + Render `PYTHON_VERSION`） | **已处理** |
| liepin-cli 隔离 | 独立 `.liepin-venv` + `bash build.sh`；主 `requirements.txt` 不含 CLI | **已处理** |
| CLI 版本冻结 | `858a62bd839d490e8745b7503961e4676a54b9d7`（`git ls-remote` main） | **已钉死** |
| 密钥 | Render Dashboard 填写 `PLUGIN_TOKEN`、`LIEPIN_USER_TOKEN`（`sync: false`） | 勿入库 |
| 测试 | `pytest -q` 全绿 | 见测试报告 |

独立路径 `/plugin/v1/jobs/collect-liepin` 仍需 Dashboard 中的 `LIEPIN_USER_TOKEN`，否则返回 503。

---

## 1. 架构要点（部署相关）

- 网关：FastAPI + uvicorn，**单进程**推荐（信号量/缓存在进程内）。
- 猎聘：独立 venv 子进程，仅允许  
  `<LIEPIN_PYTHON_EXECUTABLE> -m liepin_cli.main job search --job-name … --address … --page … --output json`
- Token：环境变量 `LIEPIN_USER_TOKEN` 继承给子进程，**禁止**写入 argv / 日志 / OpenAPI。
- 独立工具：`POST /plugin/v1/jobs/collect-liepin`，`operationId=collectLiepinJobs`。
- 原工具：`POST /plugin/v1/jobs/collect`，`operationId=collectPublicJobs`（保持不变）。

---

## 2. Render Blueprint（`render.yaml`）

已声明的猎聘相关变量：

| Key | Blueprint | 说明 |
|-----|-----------|------|
| `LIEPIN_USER_TOKEN` | `sync: false` | Dashboard 填写；勿提交 |
| `LIEPIN_CLI_TIMEOUT_SECONDS` | `45` | CLI 超时 |
| `LIEPIN_MAX_CONCURRENT` | `1` | 进程内并发 |
| `LIEPIN_MAX_KEYWORDS` | `3` | 关键词上限 |
| `LIEPIN_MAX_PAGES` | `1` | 页数上限 |
| `LIEPIN_MAX_ITEMS_LIMIT` | `60` | 内部硬顶 |
| `LIEPIN_CACHE_TTL_SECONDS` | `600` | 搜索缓存 TTL |
| `LIEPIN_OUTPUT_MAX_BYTES` | `2000000` | stdout 上限 |
| `LIEPIN_CLI_COMMIT` | `sync: false` | 填入钉死的 commit |
| `LIEPIN_FALLBACK_TO_SNAPSHOT` | `false` | 生产建议保持 false |

启动命令（已配置）：

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

健康检查：`/health`。

**不要**设置 `WEB_CONCURRENCY>1`（除非改为外部限流 + 接受每 worker 独立 CLI 配额）。

---

## 3. 构建命令（独立猎聘 venv）

Blueprint：

```text
bash build.sh
```

`build.sh` 会：

1. `pip install -r requirements.txt`（主 FastAPI 环境，**不含** liepin-cli，**不含** pytest）
2. 重建 `.liepin-venv`
3. 在独立 venv 内安装 `requirements-liepin.txt`
4. 验证 `import liepin_cli` 与 `python -m liepin_cli.main --help`

本地跑测试请另装：`pip install -r requirements-dev.txt`（Render 生产构建不安装）。

`requirements-liepin.txt` 当前为：

```text
git+https://github.com/liepin-tech-2026/liepin-cil.git@858a62bd839d490e8745b7503961e4676a54b9d7
```

（SHA 来自 `git ls-remote … refs/heads/main`，已冻结。）

运行时通过 `LIEPIN_PYTHON_EXECUTABLE`（默认 `.liepin-venv/bin/python`）执行模块调用（`asyncio.create_subprocess_exec`，无 shell）。

环境变量：

```text
PYTHON_VERSION=3.12.7
LIEPIN_PYTHON_EXECUTABLE=.liepin-venv/bin/python
LIEPIN_CLI_COMMIT=858a62bd839d490e8745b7503961e4676a54b9d7
WEB_CONCURRENCY=1
```

启动后 `/health` 应出现：

```json
{
  "liepin_cli_installed": true,
  "liepin_invocation_mode": "isolated_python_module",
  "liepin_token_configured": true,
  "liepin_provider_enabled": false,
  "liepin_cli_commit": "858a62bd839d490e8745b7503961e4676a54b9d7"
}
```

说明：`liepin_provider_enabled` 仅在 `JOB_PROVIDER=liepin_cli` 时为 true；独立 `collect-liepin` 接口不依赖该开关，但依赖 installed + token。

---

## 4. 推荐环境矩阵

### 4.1 默认（MCP / 公开源，猎聘接口可用）

| Key | 建议值 |
|-----|--------|
| `DEMO_MODE` | `false` |
| `JOB_PROVIDER` | `mcp_jobs`（保持原行为） |
| `MCP_JOBS_ENABLED` | `true` |
| `LIEPIN_USER_TOKEN` | （Dashboard 密钥） |
| `LIEPIN_PYTHON_EXECUTABLE` | `.liepin-venv/bin/python` |
| `LIEPIN_FALLBACK_TO_SNAPSHOT` | `false` |

星辰侧可同时挂两个工具：`collectPublicJobs` + `collectLiepinJobs`。

### 4.2 旧 collect 整路由切猎聘（谨慎）

| Key | 建议值 |
|-----|--------|
| `JOB_PROVIDER` | `liepin_cli` |
| 其余同上 | |

此时 `collectPublicJobs` 走软失败包装；日期过滤会带 `DATE_FILTER_NOT_SUPPORTED_BY_LIEPIN_SEARCH`。

---

## 5. Dashboard 密钥操作

1. Render → Service → Environment。
2. 设置 `PLUGIN_TOKEN`（强随机，与星辰插件 Bearer 一致）。
3. 设置 `LIEPIN_USER_TOKEN`（猎聘用户授权 Token；**仅此环境**）。
4. 确认 `LIEPIN_PYTHON_EXECUTABLE=.liepin-venv/bin/python`。
5. 设置 `PUBLIC_BASE_URL` 为公网 URL（供 OpenAPI servers）。
6. 保存并 **Manual Deploy**（若未自动部署）。

禁止：

- 把 Token 写进 README / Issue / 截图
- 在 `buildCommand` 或启动脚本中 `echo` Token
- 将 `.env` 提交 Git

---

## 6. 部署步骤（Checklist）

1. [ ] 合并/提交猎聘代码（本手册不执行 push）
2. [x] `.python-version` → `3.12.7`
3. [x] `buildCommand` → `bash build.sh`（独立 `.liepin-venv`）
4. [ ] Dashboard 填写 `PLUGIN_TOKEN` / `LIEPIN_USER_TOKEN`（确认 `LIEPIN_PYTHON_EXECUTABLE`）
5. [ ] Deploy 成功，`GET /health` 返回 `status=ok`、`liepin_cli_installed=true`、`liepin_invocation_mode=isolated_python_module`
6. [ ] 用错误 Bearer 调 collect-liepin → **401**
7. [ ] 用正确 Bearer 调 collect-liepin → **200**，`result` 为 JSON **字符串**
8. [ ] 确认内层 `data_mode=live_authorized_liepin`，`trend_eligible` 全 false
9. [ ] 回归 `POST /plugin/v1/jobs/collect`（原 MCP/demo 路径）仍正常
10. [ ] 星辰插件重新导入 OpenAPI（含 `collectLiepinJobs`）

---

## 7. 冒烟命令

```bash
export SERVICE_BASE_URL="https://<your-service>.onrender.com"
export PLUGIN_TOKEN="<dashboard-plugin-token>"

curl -sS "$SERVICE_BASE_URL/health" | jq .

curl -sS -o /tmp/liepin_unauthorized.json -w "%{http_code}\n" \
  -X POST "$SERVICE_BASE_URL/plugin/v1/jobs/collect-liepin" \
  -H "Authorization: Bearer wrong" \
  -H "Content-Type: application/json" \
  -d '{"keywords":"网络安全工程师","region":"广东","max_items":5}'

curl -sS -X POST "$SERVICE_BASE_URL/plugin/v1/jobs/collect-liepin" \
  -H "Authorization: Bearer $PLUGIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"keywords":"网络安全工程师|信息安全工程师","region":"广东","max_items":10,"start_date":"","end_date":""}' \
  | jq -r .result | jq '{data_mode,count,warnings,trend: [.jobs[].trend_eligible]}'
```

预期 HTTP 映射：

| 场景 | HTTP |
|------|------|
| Bearer 错误 | 401 |
| CLI 未安装 / Token 未配置 | 503 |
| 并发占满 | 429 |
| CLI 超时 | 504 |
| 进程/JSON/业务 code | 502 |
| 成功 | 200，`result` 为 string |

---

## 8. 星辰插件配置摘要

| 项 | 值 |
|----|-----|
| 鉴权 | HTTP Bearer = `PLUGIN_TOKEN` |
| 工具 A | `collectPublicJobs` → `/plugin/v1/jobs/collect` |
| 工具 B | `collectLiepinJobs` → `/plugin/v1/jobs/collect-liepin` |
| 响应 | 外层仅 `result: string` |
| 猎聘能力声明 | 无 JD、无发布日；不可作趋势/技能结论 |

---

## 9. 回滚

1. Dashboard 清空或轮换 `LIEPIN_USER_TOKEN` → 猎聘接口立即 503。
2. 保持 `JOB_PROVIDER=mcp_jobs`，星辰停用 `collectLiepinJobs`。
3. 如需代码回滚：回退至合入猎聘前的 commit 并重新部署。
4. 不依赖磁盘快照；`LIEPIN_FALLBACK_TO_SNAPSHOT=false` 时无本地猎聘数据依赖。

---

## 10. 运维注意

- Free 实例冷启动可能导致首次 CLI 超时（504）；可提高 `LIEPIN_CLI_TIMEOUT_SECONDS`（上限 180）。
- 缓存命中会在 warnings 中出现 `CACHE_HIT`（TTL 内）。
- 传入 `start_date`/`end_date` 不会过滤猎聘结果，仅增加 `DATE_FILTER_NOT_SUPPORTED_BY_LIEPIN_SEARCH`。
- 审计残留：Semaphore 获取超时当前写死 0.05s，高负载可能 429；见安全审计 P1。
