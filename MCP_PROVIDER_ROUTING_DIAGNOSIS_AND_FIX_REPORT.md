# MCP Provider 路由诊断与修复报告

- **时间**：2026-08-03
- **项目**：secskill-data-service（FastAPI 网关）
- **约束**：未修改 `secskill-mcp-jobs-adapter`；未暴露任何 Token 明文

---

## 1. 根因

综合判定为：

| 代码 | 含义 | 是否成立 |
|------|------|----------|
| **A** | MCP Provider 代码不存在 | **是**（修复前） |
| **B** | Provider 存在但未接入公开路由 | 修复前等价于 A |
| **E** | Provider 分支顺序错误 / 缺失 | **是**：`DEMO_MODE=false` 直接进入 public sources |
| **H** | 当前部署版本不是含 MCP 的最新提交 | **是**：本地 `HEAD=0455d1b` 与 origin/main 一致，且该提交无 MCP 集成 |

**不是**主要根因：C/D（环境变量在 Render 已配好，但代码根本不读 `JOB_PROVIDER`/`MCP_JOBS_*`）；F（无“静默回退”，而是根本没有 MCP 分支）。

现象解释：Render 上 `DEMO_MODE=false` → 走 `_collect_live` → `sources.json` 无启用源 → `NO_ENABLED_PUBLIC_SOURCES` / `data_mode=live_public` / `count=0`。Adapter 本身正常，但网关从未调用它。

---

## 2. 原错误调用链

```
POST /plugin/v1/jobs/collect  (operationId=collectPublicJobs)
  └─ collect_public_jobs()
       ├─ DEMO_MODE=true  → _collect_demo → data_mode=demo
       └─ DEMO_MODE=false → _collect_live → load_sources()
                              └─ 无 enabled json_feed
                                   → warnings=[NO_ENABLED_PUBLIC_SOURCES]
                                   → data_mode=live_public
                                   → provider=null
```

关键字检索（修复前）：`JOB_PROVIDER` / `MCP_JOBS_*` / `mcp_jobs` / `mcp_adapter_fixture` / `live_public_mcp` **均不存在**。

---

## 3. 修复后调用链

```
POST /plugin/v1/jobs/collect  (operationId=collectPublicJobs, result:String 不变)
  └─ collect_public_jobs()
       ├─ ① DEMO_MODE=true
       │     → demo → data_mode=demo
       ├─ ② DEMO_MODE=false AND JOB_PROVIDER=mcp_jobs AND MCP_JOBS_ENABLED=true
       │     → call_mcp_jobs_adapter()
       │          POST {MCP_JOBS_BASE_URL}/internal/v1/jobs/search
       │          Header: X-Internal-Token / Content-Type
       │     → mode=fixture → data_mode=mcp_adapter_fixture
       │     → mode=live    → data_mode=live_public_mcp
       │     → 失败 + FALLBACK=true  → data_mode=fallback_demo
       │     → 失败 + FALLBACK=false → data_mode=live_public_mcp_failed
       │     （失败不进入 public sources）
       └─ ③ 其他
             → _collect_live → data_mode=live_public
```

---

## 4. 修改文件清单

| 文件 | 变更 |
|------|------|
| `app.py` | MCP 配置读取、布尔解析、Adapter 调用、路由优先级、安全日志、结果映射 |
| `tests/test_mcp_provider.py` | **新增** MCP 路由/映射/失败回退/OpenAPI 契约测试 |
| `tests/conftest.py` | 默认关闭 MCP，避免污染原 demo 测试 |
| `.env.example` | 补充 MCP 相关变量模板（无真实密钥） |
| `render.yaml` | 声明 MCP 环境变量；`MCP_JOBS_TOKEN`/`PLUGIN_TOKEN` 为 `sync:false` |
| `secskill-data-service.openapi.json` | 重新导出 |

---

## 5. 核心分支逻辑

1. `DEMO_MODE` → demo  
2. `JOB_PROVIDER.strip().lower()=="mcp_jobs"` 且 `_parse_bool(MCP_JOBS_ENABLED)` → Adapter  
3. 否则 → public sources  

布尔解析：**禁止** `bool("false")`；支持 true/1/yes/on 与 false/0/no/off/空串。

---

## 6. 环境变量读取方式

- **模块导入时**读取一次（与原 `PLUGIN_TOKEN`/`DEMO_MODE` 一致），Render 启动注入即可生效。  
- 测试通过 `monkeypatch` 覆盖模块级变量。  
- `MCP_JOBS_BASE_URL` 执行 `rstrip("/")`；搜索路径只拼接一次 `/internal/v1/jobs/search`。

安全日志仅记录：`demo_mode`、`job_provider`、`mcp_jobs_enabled`、`adapter_base_url_configured`、`adapter_token_configured`、timeout、page；请求后记录 `selected_provider` / `adapter_response_mode` / `adapter_http_status` / `returned_job_count`。

---

## 7. 测试结果

```text
pytest -q
46 passed, 1 warning
```

（警告来自 Starlette TestClient / httpx 弃用提示，非业务失败）

`python -m py_compile app.py`：通过  
`ruff check app.py tests/test_mcp_provider.py`：通过  
`mypy app.py`：通过  

---

## 8. OpenAPI 验证

- `operationId` = `collectPublicJobs`  
- `result.type` = `string`  
- 路径仍为 `POST /plugin/v1/jobs/collect`  

---

## 9. 本地 Git HEAD

- `HEAD`：`0455d1baba12c9c59892710b2631bdcdab36b48f`  
- 分支：`main`，与 `origin/main` **修复前一致**；**修复后工作区有未提交变更**  
- remote：`https://github.com/ViewFuture/secskill-data-service.git`  

---

## 10. Render 重新部署步骤

1. 将本次修改 `git commit` 并 `git push` 到 `main`（或你的部署分支）。  
2. 若 Auto-Deploy 已开，等待 Render 自动构建；否则在 Dashboard 手动 **Manual Deploy → Deploy latest commit**。  
3. 确认环境变量（Dashboard）：  
   - `DEMO_MODE=false`  
   - `JOB_PROVIDER=mcp_jobs`  
   - `MCP_JOBS_ENABLED=true`  
   - `MCP_JOBS_BASE_URL=https://secskill-mcp-jobs-adapter.onrender.com`  
   - `MCP_JOBS_TOKEN` = Adapter 的 `INTERNAL_TOKEN`（仅控制台配置）  
   - `MCP_JOBS_TIMEOUT_SECONDS=180`  
   - `MCP_JOBS_FALLBACK_TO_DEMO=true`  
   - `MCP_JOBS_PAGE=1`  
   - `DATE_FILTER_MODE=soft`  
   - `PLUGIN_TOKEN` 已设置  
4. 查看启动日志应出现 `runtime_config ... mcp_jobs_enabled=True adapter_base_url_configured=True adapter_token_configured=True`（**无 Token 明文**）。  
5. 用下方 curl 验证。

---

## 11. 部署后预期内部结果（`result` JSON）

```json
{
  "count": 3,
  "data_mode": "mcp_adapter_fixture",
  "provider": "mcp-jobs",
  "provider_version": "1.4.0",
  "warnings": [
    "FIXTURE_DATA_NOT_FOR_REAL_TREND_CLAIMS",
    "UNKNOWN_SOURCE_DOMAIN"
  ]
}
```

（另含 `batch_preview` / `raw_items` / `source_ledger`；外层仍为 `{ "result": "<string>" }`）
