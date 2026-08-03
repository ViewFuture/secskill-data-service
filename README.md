# SecSkill_Data_Service

为「岗安智练 SecSkill Agent」提供公开岗位数据采集接口的 FastAPI 后端，可部署到 [Render](https://render.com)，并作为星辰 Agent 自定义插件使用。

核心能力：

- `GET /` / `GET /health`：服务探测
- `POST /plugin/v1/jobs/collect`：公开岗位采集（`operationId` = **`collectPublicJobs`**）
- 工具响应外层固定为 **`{ "result": "<JSON字符串>" }`**，即 **`result` 类型为 String**（不是 Object/Array）

> **重要**：当 `DEMO_MODE=true` 时，数据来自本地演示 fixtures，**不得用于真实技能趋势分析结论**。  
> **明确说明**：Render 本地文件系统不能作为岗位快照持久化数据库；实例重启/重新部署会丢失运行时写入。

---

## 安全边界

- `fixtures/jobs.json` **仅用于功能联调**，公司名为「演示企业A/B/…」，`source_url` 使用 `example.org`，不得当作真实招聘信息，也不得据此输出真实趋势结论。
- `sources.json` 为公开数据源白名单结构，默认 `enabled=false`，不内置任何真实招聘网站。
- `.env` / Render 控制台中的 `PLUGIN_TOKEN` 为密钥：不入库、不写进 README、不写进 `render.yaml` 实际值。
- 接口日志与响应中不得回显 Token。
- 不实现商业招聘平台爬虫；不配置数据库与持久磁盘。

---

## 项目目录

```text
secskill-data-service/
├─ app.py                              # FastAPI 应用入口
├─ requirements.txt                    # Python 依赖
├─ .python-version                     # Python 3.11.x
├─ .gitignore
├─ .env.example                        # 环境变量模板（无真实密钥）
├─ render.yaml                         # Render Blueprint（可选）
├─ sources.json                        # 公开数据源白名单（默认禁用）
├─ fixtures/
│  └─ jobs.json                        # 演示岗位数据（仅联调）
├─ scripts/
│  ├─ export_openapi.py                # 导出/校验 OpenAPI
│  └─ smoke_test.py                    # 公网/本地冒烟测试
├─ tests/                              # pytest
├─ secskill-data-service.openapi.json  # 导出后的 OpenAPI（可导入星辰）
└─ README.md
```

根目录部署必备文件：`app.py`、`requirements.txt`、`.python-version`、`.env.example`、`sources.json`、`fixtures/jobs.json`。

---

## 本地开发（Windows PowerShell）

### 1. 创建虚拟环境

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

若执行策略拦截，可先（当前用户）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

### 3. 创建 `.env`

```powershell
Copy-Item .env.example .env
```

### 4. 生成随机 `PLUGIN_TOKEN` 并写入 `.env`

```powershell
$token = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
(Get-Content .env) -replace '^PLUGIN_TOKEN=.*', "PLUGIN_TOKEN=$token" | Set-Content .env -Encoding utf8
Write-Host "PLUGIN_TOKEN has been written to .env (not shown here)."
```

也可手动编辑 `.env`，字段参考 `.env.example`：

- `PLUGIN_TOKEN`
- `PUBLIC_BASE_URL=http://127.0.0.1:8000`
- `DEMO_MODE=true`
- `SOURCE_FILE=sources.json`
- `DEMO_FILE=fixtures/jobs.json`
- `REQUEST_TIMEOUT_SECONDS=20`

### 5. 本地启动

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

### 6. 本地地址

- Health：<http://127.0.0.1:8000/health>
- Swagger Docs：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>

### 7. PowerShell 本地调用示例

先从 `.env` 读取 Token（不要把 Token 打到聊天/截图里）：

```powershell
$env:PLUGIN_TOKEN = (Get-Content .env | Where-Object { $_ -match '^PLUGIN_TOKEN=' }) -replace '^PLUGIN_TOKEN=',''
Invoke-RestMethod -Uri http://127.0.0.1:8000/health -Method Get

$body = @{
  keywords   = "网络安全运维 安全运营 SOC"
  region     = "广东"
  start_date = "2026-05-01"
  end_date   = "2026-08-01"
  max_items  = 20
} | ConvertTo-Json

$headers = @{ Authorization = "Bearer $env:PLUGIN_TOKEN" }
$resp = Invoke-RestMethod -Uri http://127.0.0.1:8000/plugin/v1/jobs/collect -Method Post -Headers $headers -ContentType "application/json" -Body $body
$inner = $resp.result | ConvertFrom-Json
$inner | Select-Object count, data_mode, warnings
```

说明：外层只有字符串字段 `result`；需再 `ConvertFrom-Json` / `json.loads` 才能读到 `count`、`data_mode` 等。

---

## 测试

### pytest

```powershell
pytest -q
```

### 导出 OpenAPI

```powershell
python scripts/export_openapi.py
```

成功后生成：`secskill-data-service.openapi.json`。  
导出脚本会校验：

- `info.title` = `SecSkill_Data_Service`
- 存在 `POST /plugin/v1/jobs/collect`
- `operationId` = **`collectPublicJobs`**
- 请求体含 `keywords` / `region` / `start_date` / `end_date` / `max_items`
- 200 响应中 **`result` 类型为 string**
- 存在 HTTP Bearer 安全方案

### 冒烟测试 `smoke_test.py`

需服务已启动：

```powershell
$env:SERVICE_BASE_URL = "http://127.0.0.1:8000"
$env:PLUGIN_TOKEN = (Get-Content .env | Where-Object { $_ -match '^PLUGIN_TOKEN=' }) -replace '^PLUGIN_TOKEN=',''
python scripts/smoke_test.py
```

脚本会请求 `/health` 与 `/plugin/v1/jobs/collect`，并打印 `data_mode` / `count` / `source_ledger` 条数 / `warnings`；**不会打印** `PLUGIN_TOKEN`。

---

## 提交到 GitHub

```powershell
git init
git add .
git status
git commit -m "feat: initialize SecSkill_Data_Service for Render and Xingchen plugin"
git branch -M main
git remote add origin https://github.com/<your-org-or-user>/secskill-data-service.git
git push -u origin main
```

提交前确认：

- 不要 `git add .env`
- 不要提交真实 Token / 招聘网站账号密码
- `.gitignore` 已排除 `.venv`、`.env`、缓存目录

---

## 部署到 Render

可用两种方式：**Blueprint（`render.yaml`）** 或 **控制台手工创建 Web Service**。二者并存，不依赖 Blueprint 也能手工部署。

### 方式 A：Blueprint

1. Render Dashboard → New → Blueprint
2. 连接本仓库，识别根目录 `render.yaml`
3. 首次创建时按提示填写：
   - `PLUGIN_TOKEN`（自行生成的随机串）
   - `PUBLIC_BASE_URL`（部署成功后的公网 URL，例如 `https://secskill-data-service.onrender.com`，**不要在仓库写死最终域名**）
4. 其余变量已由 Blueprint 声明（`DEMO_MODE=true` 等）

### 方式 B：手工创建 Web Service（字段表）

| 字段 | 建议值 |
|------|--------|
| Language / Runtime | Python |
| Branch | `main`（或你的默认分支） |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Auto Deploy | Enabled / On |

**Environment Variables：**

| Key | Value | 说明 |
|-----|-------|------|
| `PLUGIN_TOKEN` | （控制台填写，勿入库） | Bearer 鉴权密钥 |
| `PUBLIC_BASE_URL` | （部署后填写公网 URL） | 写入 OpenAPI `servers` |
| `DEMO_MODE` | `true` | 演示模式；**不得用于真实趋势分析** |
| `SOURCE_FILE` | `sources.json` | 白名单路径 |
| `DEMO_FILE` | `fixtures/jobs.json` | 演示数据路径 |
| `REQUEST_TIMEOUT_SECONDS` | `20` | 外部请求超时秒数 |

注意：

- **Start Command 必须绑定 `0.0.0.0` 与 `$PORT`**，不要写死 `8000`，否则易出现 PORT 绑定错误 / 健康检查失败。
- **不配置数据库、不配置持久磁盘**。
- Render 临时文件系统不能当作岗位快照持久化库。

### 部署后公网测试

将 `<SERVICE_URL>` 换成你的 Render 公网地址：

```powershell
$env:SERVICE_BASE_URL = "https://<SERVICE_URL>"
$env:PLUGIN_TOKEN = "<在本机环境变量中设置，勿写入仓库>"
python scripts/smoke_test.py
```

或：

```powershell
Invoke-RestMethod -Uri "$env:SERVICE_BASE_URL/health" -Method Get
```

也可用浏览器打开：

- `https://<SERVICE_URL>/health`
- `https://<SERVICE_URL>/docs`

---

## 星辰 Agent 插件导入

1. 本地执行：

```powershell
python scripts/export_openapi.py
```

2. 得到 `secskill-data-service.openapi.json`。
3. 在星辰自定义插件中导入该 OpenAPI。
4. 插件鉴权选择 **HTTP Bearer**，Token 与 Render / `.env` 中的 `PLUGIN_TOKEN` 保持一致。
5. 确认工具：
   - **operationId** = **`collectPublicJobs`**
   - 路径 = `POST /plugin/v1/jobs/collect`
   - 输出约定：外层 **`result: String`**（内部再解析 JSON，含 `count`、`data_mode`、`batch_preview`、`raw_items`、`source_ledger`、`warnings`）
6. 将插件 Base URL / Server 指向 Render 公网地址（与 `PUBLIC_BASE_URL` 一致）。

---

## 关键约定（务必遵守）

| 项 | 约定 |
|----|------|
| operationId | `collectPublicJobs`（严格相等） |
| 工具输出 | `{ "result": "<string>" }`，**result 必须是 String** |
| DEMO_MODE=true | 仅演示/联调，**不得用于真实趋势分析** |
| 持久化 | Render 本地磁盘 **不是** 岗位快照数据库 |

---

## 常见错误

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| **401** | 缺少 `Authorization`、Token 错误、或前后空格 | 使用 `Bearer <PLUGIN_TOKEN>`；核对 Render 环境变量 |
| **422** | 请求体校验失败（如 `max_items=0/201`、缺字段） | 按 Schema 传参；`max_items` 范围 1–200 |
| **502** | 进程崩溃、启动命令错误、依赖安装失败 | 查看 Render Logs；确认 Build/Start Command |
| **找不到 operationId** | 导入了错误 OpenAPI，或未导出最新 Schema | 重新运行 `python scripts/export_openapi.py` 并确认 `collectPublicJobs` |
| **result 类型不匹配** | 星辰期望 String，却按 Object 解析外层 | 外层只读 `result` 字符串，再 `json.loads` |
| **Render 启动命令错误** | 写成 `python app.py` 或未使用 uvicorn | 使用 `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| **PORT 绑定错误** | 绑定 `127.0.0.1` 或写死端口 | 必须 `--host 0.0.0.0 --port $PORT` |

---

## 许可证与声明

本仓库演示数据与接口仅供「岗安智练」教学/联调场景使用。请勿将演示岗位数据表述为真实招聘市场结论。
