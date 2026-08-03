# 猎聘 liepin-cli 集成发布前安全审计（R6）

- **审计时间**：2026-08-03 19:57 +0800
- **审计范围**：`providers/liepin/**`、`app.py` 猎聘路由与健康检查、OpenAPI、`.env.example`、`render.yaml`、测试与 Git 历史
- **工作区 HEAD（已推送）**：`94f9503`；猎聘集成代码仍在工作区未提交
- **说明**：本报告不含任何真实 Token 明文；不自动 push

---

## 总评

| 维度 | 结论 |
|------|------|
| 只读执行面（仅 `liepin-cli job search`） | **通过** |
| Token 入库 / 日志 / OpenAPI 泄露 | **通过**（仓库与历史未见真实猎聘 Token） |
| 危险子进程 API / 写操作路由 | **通过** |
| 输入边界 / `result: string` / 原接口兼容 | **通过** |
| Python 固定 3.12.7 | **已通过**（`.python-version` / Render `PYTHON_VERSION` = `3.12.7`） |
| liepin-cli 固定到具体 commit 并参与构建 | **已通过**（钉死 `858a62bd…`；`requirements-liepin.txt` + `scripts/install_liepin_cli.sh` 进入 `buildCommand`） |

**发布建议**：**P0 已解除**。只读安全边界达标；部署时仍需在 Dashboard 配置 `LIEPIN_USER_TOKEN`，并按部署手册做公网冒烟后再放量。

---

## 检查清单（14 项）

| # | 检查项 | 结论 | 证据摘要 |
|---|--------|------|----------|
| 1 | 敏感词：`x-user-token` / `LIEPIN_USER_TOKEN` / `Authorization` / `Cookie` | **通过（可控）** | 全仓无 `x-user-token`、无 `Cookie`；`LIEPIN_USER_TOKEN` 仅出现在 env 读取/示例空值/注释；`Authorization` 仅用于网关 `PLUGIN_TOKEN` Bearer |
| 2 | 真实 Token 未进入 Git / `.env` / README / fixture / 日志 | **通过** | 无 `.env` 文件；`.gitignore` 含 `.env`；历史中仅 `PLUGIN_TOKEN=change-me`；fixture/测试为假 Token；日志只记 `token_configured` 布尔与脱敏错误码 |
| 3 | `shell=True` / `os.system` / `subprocess.run` / `job apply` / `resume update` / `resume add` | **通过** | 生产代码无上述危险调用；写操作字符串仅出现在禁止断言测试中 |
| 4 | 运行路径仅 `liepin-cli job search` | **通过** | `cli_runner` 固定 argv + `_assert_safe_search_argv`；拒绝非 `job search` |
| 5 | 参数经列表传给 `create_subprocess_exec` | **通过** | `await asyncio.create_subprocess_exec(*argv, ...)`，无 shell 拼接 |
| 6 | keywords / region / page / max_items 有边界 | **通过** | 见下方「边界表」 |
| 7 | OpenAPI 不暴露 Token 字段 | **通过** | `CollectLiepinJobsRequest` 仅 keywords/region/max_items/start_date/end_date；安全方案为 HTTPBearer（插件 Token） |
| 8 | 健康检查只返回 `token_configured` 布尔 | **通过** | `liepin_token_configured: bool`；另有 installed/enabled/commit 元数据，**不含 Token 值** |
| 9 | 错误均脱敏 | **通过** | stderr 读取后丢弃；HTTP `message` 为固定短句；不回传 env/堆栈/CLI 原文 |
| 10 | `result` 为 String | **通过** | `ToolResponse.result: str` + `json.dumps`；OpenAPI `type: string` |
| 11 | 原接口未被破坏 | **通过** | `collectPublicJobs` 仍在；原有 + 新增测试共 **78 passed** |
| 12 | Python 版本固定为 3.12.7 | **通过（已修复）** | `.python-version` = `3.12.7`；`render.yaml` 含 `PYTHON_VERSION=3.12.7` |
| 13 | 依赖固定到 liepin-cli 具体 commit | **通过（已修复）** | 钉死 `858a62bd839d490e8745b7503961e4676a54b9d7`；构建执行 `install_liepin_cli.sh` |
| 14 | 测试全部通过 | **通过** | `pytest -q` → **78 passed**；`ruff` / `mypy` 通过 |

---

## 1. 敏感词扫描

检索命令（工作区，排除 `.venv` / `.git` / 嵌套误克隆目录）：

```text
x-user-token / X-User-Token     → 0 命中
Cookie / Set-Cookie             → 0 命中
LIEPIN_USER_TOKEN               → 配置读取、.env.example 空值、render.yaml sync:false、注释
Authorization                   → 插件 Bearer（app/README/tests/smoke），非猎聘 Token 头
```

**判定**：未发现把猎聘用户 Token 写入请求头名 `x-user-token` 或 Cookie 的代码路径。Token 仅通过继承环境变量 `LIEPIN_USER_TOKEN` 交给子进程。

---

## 2. Token 落盘与历史

| 位置 | 结果 |
|------|------|
| 工作区 `.env` | **不存在** |
| Git 跟踪 | `.env` 未跟踪；`.gitignore` 含 `.env` / `.env.*`（保留 `.env.example`） |
| Git 历史（`LIEPIN_USER_TOKEN=` 非空、长 Bearer、`x-user-token`） | **未发现真实密钥** |
| `.env.example` | `LIEPIN_USER_TOKEN=` 空；`PLUGIN_TOKEN=change-me` 占位 |
| README | 仅占位符 `$PLUGIN_TOKEN` / `<your-plugin-token>` |
| 测试 fixture | `pytest-plugin-token-do-not-leak`、`adapter-internal-token-for-tests` 等假值 |
| 日志 | `token_configured=%s`（bool）；不打印 Token / Authorization / env |

**残余风险**：聊天/运维侧曾可能粘贴过真实 `PLUGIN_TOKEN`；属流程风险，**当前仓库内容未见入库**。建议轮换已暴露的插件 Token。

---

## 3. 危险 API 与写操作

| 模式 | 生产代码 |
|------|----------|
| `shell=True` | 无 |
| `os.system` | 无 |
| `subprocess.run` | 无 |
| `job apply` / `resume update` / `resume add` | 无路由、无 CLI 调用 |

子进程唯一入口：`providers/liepin/cli_runner.py` → `asyncio.create_subprocess_exec`。

OpenAPI paths 仅：`/`、`/health`、`/plugin/v1/jobs/collect`、`/plugin/v1/jobs/collect-liepin`。

---

## 4–5. 允许的 CLI 形态

固定 argv：

```text
liepin-cli job search --job-name <kw> --address <region> --page <n> --output json
```

硬校验：

- binary ∈ `{liepin-cli}`
- 子命令必须为 `job` + `search`
- 长度与 flag 位次固定（11 元）
- `FORBIDDEN_CLI_TOKENS` 覆盖 apply/resume/auth/skill 等

调用：`create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE, env=os.environ.copy())` —— **列表传参，非 shell**。

---

## 6. 输入边界

| 参数 | 边界 |
|------|------|
| `keywords`（HTTP） | `min_length=1`, `max_length=200` |
| 拆分后单关键词 | 长度 2–40；分隔 `| , ， \n`；去重；截断至 `LIEPIN_MAX_KEYWORDS`（默认 3，env 钳制 1–10） |
| `region` | `min_length=1`, `max_length=30`（默认「广东」） |
| `max_items` | `ge=1`, `le=60`；内部再与 `LIEPIN_MAX_ITEMS_LIMIT`（默认 60，env 1–200）取小 |
| `page` | 由 `range(1, max_pages+1)` 生成；`LIEPIN_MAX_PAGES` 默认 1，env 钳制 1–5 |
| stdout | `LIEPIN_OUTPUT_MAX_BYTES`（默认 2MB，钳制 64KB–8MB） |
| CLI 超时 | `LIEPIN_CLI_TIMEOUT_SECONDS`（默认 45，钳制 5–180） |

---

## 7–8. OpenAPI 与健康检查

- 请求模型无 `token` / `LIEPIN_*` 字段。
- 响应 `ToolResponse.result`：`type: string`。
- `/health` 猎聘字段：`liepin_cli_installed`、`liepin_token_configured`（**bool**）、`liepin_provider_enabled`、`liepin_cli_commit`（版本元数据字符串，非密钥）。

---

## 9. 错误脱敏

- CLI stderr：限长读取后丢弃（`_stderr`），不进入 HTTP。
- `LiepinCliError.message`：固定英文短句（如 `Liepin CLI timed out`）。
- HTTP：`detail={error_code, message}`，映射为公开错误码（503/429/504/502），无堆栈、无 Token。

---

## 10–11. 契约与兼容

- 外层恒为 `{"result":"<JSON string>"}`。
- 内层猎聘：`data_mode=live_authorized_liepin`；`trend_eligible` 等为 false。
- `POST /plugin/v1/jobs/collect`（`collectPublicJobs`）保留；`JOB_PROVIDER=liepin_cli` 时走软失败包装，不替代默认 MCP/demo 路径除非显式配置。

---

## 12–13. P0 修复记录

### P0-1 Python 钉死 3.12.7 — **已修复**

| 项 | 值 |
|----|-----|
| 仓库 `.python-version` | `3.12.7` |
| Render `PYTHON_VERSION` | `3.12.7` |

### P0-2 liepin-cli commit 钉死并进入构建 — **已修复**

| 项 | 值 |
|----|-----|
| 上游 | `https://github.com/liepin-tech-2026/liepin-cil` |
| 钉死 commit | `858a62bd839d490e8745b7503961e4676a54b9d7` |
| 单一事实来源 | `providers/liepin/cli_pin.py` |
| 安装清单 | `requirements-liepin.txt` |
| 构建 | `bash scripts/install_liepin_cli.sh`（校验 pin 一致性后 pip install） |
| 默认 `LIEPIN_CLI_COMMIT` | 与上述 SHA 相同（Blueprint / `.env.example` / 配置默认值） |

未安装 CLI 时接口仍返回 503（保持）。

---

## P1 / P2 风险（不阻塞「只读安全」结论，但影响稳定性）

| 级别 | 项 | 说明 |
|------|-----|------|
| P1 | Semaphore `wait_for(..., timeout=0.05)` 写死 | 易在抖动下误报 `LIEPIN_PROVIDER_BUSY`；应改为可配置 |
| P1 | 多 worker | 进程内信号量/缓存不跨进程；`WEB_CONCURRENCY>1` 放大 CLI 并发 |
| P1 | 全量 `os.environ.copy()` 交给子进程 | Token 传递正确，但扩大子进程可见密钥面；可改为最小 env 白名单 |
| P2 | 嵌套目录 `secskill-data-service/` | 已 gitignore，建议本地删除以免误操作 |
| P2 | 依赖版本为 `>=` | Python 包未锁 hash；与 CLI commit 钉死是不同问题 |
| P2 | 本机 PATH 已有 `liepin-cli` | 开发机「installed=true」不代表 Render 已安装 |

---

## 审计命令备忘

```bash
rg -n 'x-user-token|LIEPIN_USER_TOKEN|Authorization|Cookie' .
rg -n 'shell\s*=\s*True|os\.system\(|subprocess\.run\(|job apply|resume update|resume add' .
rg -n 'create_subprocess_exec|liepin-cli' providers app.py
git log -p -G 'LIEPIN_USER_TOKEN=[^[:space:]]+|x-user-token' --all
python -m pytest -q
ruff check app.py providers tests
mypy app.py providers
```

---

## 结论

猎聘集成在**执行面最小化、Token 不入库不回显、无 apply/resume、错误脱敏、OpenAPI/原接口契约**方面达到发布安全基线。

**P0 已关闭。** 配置好 `LIEPIN_USER_TOKEN` 并完成 Render 公网冒烟后，可按部署手册灰度猎聘流量。
