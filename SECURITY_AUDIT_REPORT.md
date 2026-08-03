# SecSkill_Data_Service 提交前安全审计报告

- **审计时间**：2026-08-03
- **角色**：安全审计员 / 高级 Python 工程师
- **范围**：鉴权、SSRF、输入校验、异常暴露、演示数据边界、OpenAPI/Render 契约、测试与文档一致性
- **说明**：本报告不包含任何 Token 明文

---

## 审计后验证

| 命令 | 结果 |
|------|------|
| `python -m py_compile app.py` | 通过 |
| `pytest -q` | **36 passed**（1 条依赖库 Starlette/TestClient 警告） |
| `python scripts/export_openapi.py` | 通过，已生成 `secskill-data-service.openapi.json` |

---

## P0：必须修复

| 编号 | 问题 | 处置 |
|------|------|------|
| P0-1 | `httpx` 原 `follow_redirects=True`，重定向目标未再做公网 HTTPS/私网校验，存在 SSRF 绕过窗口 | **已修复**：改为手动跟随有限次重定向（`MAX_REDIRECTS=3`），每一跳调用 `validate_public_https_url` |
| P0-2 | 外部 Feed 响应体无字节上限，恶意大包可导致内存压力 | **已修复**：流式读取并限制 `MAX_FEED_BYTES=1MiB`；超限抛出受控错误，不拖垮整服务 |

---

## P1：部署前修复

| 编号 | 问题 | 处置 |
|------|------|------|
| P1-1 | URL 允许 `user:pass@host` 形态，增加凭证嵌入与异常解析面 | **已修复**：拒绝含 userinfo 的 URL |
| P1-2 | 岗位字段/描述无截断，`max_items` 虽限条数但单条可膨胀响应 | **已修复**：截断 title/company/region/description/skills/source_url |
| P1-3 | 请求字段缺少长度上限（keywords/region/date 字符串） | **已修复**：Pydantic `max_length`（参数名不变） |
| P1-4 | DNS 失败异常原文可能进入 `ValueError` 链条 | **已修复**：对外仅保留泛化信息；业务响应仍只回异常类型名 |
| P1-5 | `.env.example` 超时为 15，与 `render.yaml`/README 的 20 不一致 | **已修复**：示例改为 `20` |

---

## P2：后续优化

| 编号 | 建议 |
|------|------|
| P2-1 | DNS 解析与实际连接之间仍有 TOCTOU/DNS rebinding 理论窗口；可进一步改为解析后固定 IP 建连并校验 `Host` |
| P2-2 | 可增加日期跨度上限（例如 ≤366 天）以防超宽查询 |
| P2-3 | 可为 live 模式增加每源并发/全局限流 |
| P2-4 | Starlette `TestClient` 弃用 `httpx`、建议 `httpx2` 的依赖警告，待上游稳定后升级 |
| P2-5 | 首次 `git init` 后再次确认 `.env` 未被追踪（当前工作区可能尚未建库） |
| P2-6 | 部分内部辅助函数可补更细的 docstring 示例；非阻塞 |

---

## 已通过项

| # | 审计项 | 结论 |
|---|--------|------|
| 1 | Bearer Token 泄露面 | **通过**：响应/ledger/warnings 不回显 Token；`hmac.compare_digest` 比对；测试覆盖“响应与日志不含明文” |
| 2 | `.env` 是否被 `.gitignore` 排除 | **通过**：`.gitignore` 含 `.env` |
| 3 | 硬编码密钥 | **通过**：无真实密钥；仅占位符 `change-me` / `sync: false` |
| 4 | 客户端任意 URL | **通过**：请求模型无 URL 字段；live 仅读白名单 `sources.json` |
| 5 | SSRF 风险 | **通过（修复后）**：HTTPS + 主机/解析 IP 校验 + 重定向再校验 |
| 6 | 拒绝私有/回环/链路本地/非 HTTPS | **通过**：`is_private` / `loopback` / `link_local` / `reserved` / `multicast` / `unspecified`；非 HTTPS 拒绝 |
| 7 | DNS 解析后 IP 检查 | **通过**：`getaddrinfo` 结果逐地址检查 |
| 8 | 外部请求超时 | **通过**：`REQUEST_TIMEOUT_SECONDS` + `httpx` timeout |
| 9 | `max_items` 限制 | **通过**：`ge=1, le=200` |
| 10 | 日期范围验证 | **通过**：格式校验、起止顺序校验；非法发布日期记录过滤 |
| 11 | 响应体规模限制 | **通过（修复后）**：Feed 字节上限 + 字段截断 + `max_items` |
| 12 | 外部异常导致整体崩溃 | **通过**：单源 `try/except`，失败写入 ledger/warnings 后继续 |
| 13 | 敏感异常细节暴露 | **通过**：对客户端仅异常类型名；不回传 Token/堆栈 |
| 14 | 真实招聘平台爬虫 | **通过**：未发现；仅白名单 `json_feed` |
| 15 | 演示数据标记 | **通过**：`data_mode=demo`、`DEMO_DATA_NOT_FOR_REAL_TREND_CLAIMS`、fixtures/README 声明 |
| 16 | `result` 稳定为 String | **通过**：`ToolResponse.result: str` + `json.dumps` |
| 17 | `operationId=collectPublicJobs` | **通过**：路由与导出脚本双校验 |
| 18 | Render `$PORT` 启动命令 | **通过**：`uvicorn app:app --host 0.0.0.0 --port $PORT` |
| 19 | 类型标注 | **通过**：对外/核心函数具备注解；模型字段完整 |
| 20 | 未使用导入/死代码 | **通过**：未见明显未使用导入或死代码 |
| 21 | 测试覆盖关键失败路径 | **通过**：401/500 Token、400 日期、422 边界、去重、非法日期过滤、SSRF/重定向/超大包（mock） |
| 22 | README 与代码一致 | **通过（对齐后）**：路径、operationId、result:String、DEMO 边界、Render 命令与超时示例一致 |

---

## 本次代码变更摘要（仅明确问题）

1. `fetch_json_feed`：关闭自动跟随重定向 → 有限次手动跟随并重验 URL  
2. 增加 Feed 响应字节上限与字段截断  
3. 拒绝 URL userinfo  
4. 请求字段长度约束（不改参数名）  
5. 淡化 DNS 失败异常细节  
6. 补充安全测试；对齐 `.env.example` 超时值  

**未改动**：接口路径、`operationId`、`result:String` 契约、`DEMO_MODE`、星辰参数名；未引入数据库/复杂框架；未实现商业招聘爬虫。

---

## 总评

**提交前审计结论：P0/P1 已修复并回归通过，可进入部署准备。**  
剩余项均为 P2 增强建议，不阻断当前交付。
