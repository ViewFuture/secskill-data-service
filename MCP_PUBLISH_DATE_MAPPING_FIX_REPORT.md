# MCP publish_date 映射修复报告

- **时间**：2026-08-03
- **项目**：secskill-data-service
- **问题**：Adapter 返回 `publish_time_raw="2026-07-01"`，网关 `raw_items.publish_date` 为空

---

## 1. 定位

| 项 | 位置 |
|----|------|
| 转换入口 | `app.py` → `_map_adapter_jobs()` |
| 统一规范化 | `app.py` → `normalize_item()` |
| 修复前取值 | 仅 `raw.get("publish_date") or raw.get("date")` |
| 结果 | Adapter 常用字段 `publish_time_raw` 被忽略 → `publish_date=""` |

---

## 2. 修复内容

新增：

- `PUBLISH_DATE_CANDIDATE_KEYS` 优先级：
  1. `publish_date`
  2. `publish_time`
  3. `publish_time_raw`
  4. `posted_at`
  5. `date`
- `extract_publish_date_raw()`：按优先级取原始值（**不读** `collected_at`）
- `normalize_publish_date()`：规范为 `YYYY-MM-DD`；无法解析则 `""`，**绝不伪造当前时间**
- 输出字段：
  - `publish_date`：规范化结果
  - `publish_date_raw`：原始候选值
  - `trend_eligible`：有可解析日期为 `true`，否则 `false`

`DATE_FILTER_MODE=soft`（`_apply_mcp_date_filter`）：

- 日期可解析：按请求 `start_date`/`end_date` 做区间过滤
- 日期缺失：保留岗位，`trend_eligible=false`，不伪造日期
- warnings 增加：`MISSING_PUBLISH_DATE_COUNT:<n>`

`hard`：缺失或越界日期直接丢弃。

---

## 3. 契约保持

- `POST /plugin/v1/jobs/collect`
- `operationId=collectPublicJobs`
- 外层 `{ "result": "<JSON字符串>" }`
- `count` / `provider` / `data_mode` 语义不变
- 新增字段为向后兼容扩展，不破坏原有字段

---

## 4. 测试

`pytest -q` → **51 passed**（1 条依赖库警告）

覆盖：

- `publish_time_raw=2026-07-01` → `publish_date=2026-07-01`
- 带时间戳规范化
- 缺失日期 → 空串 + `trend_eligible=false`
- 不使用 `collected_at` 顶替
- soft 模式保留缺日期岗位并写入 `MISSING_PUBLISH_DATE_COUNT`
- MCP fixture 契约：`count` / `provider` / `data_mode` / `result:String`

---

## 5. 修改文件

- `app.py`
- `tests/test_mcp_provider.py`
- `MCP_PUBLISH_DATE_MAPPING_FIX_REPORT.md`（本文件）

---

## 6. 部署建议

提交并推送后，Render 重新部署。预期 Adapter fixture 岗位的 `publish_date` 将正确映射自 `publish_time_raw`（例如 `2026-07-01`）。
