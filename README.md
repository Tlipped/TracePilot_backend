# TracePilot Backend

TracePilot Backend 是 TracePilot 多智能体 DApp 漏洞定位系统的后端服务。它负责启动分析任务、调度多 Agent 流程、获取链上 Trace 与合约数据、实时推送 Agent 日志，并持久化任务、报告和审计证据。

论文背景：`TracePilot: Self-verifiable Framework for Decentralized Applications Fault Localization across Transactions`，已被 ISSTA 2026 接收。

## 核心能力

- **跨交易漏洞定位**：面向由多笔交易共同触发的 DApp 漏洞，分析攻击交易、辅助交易和关键调用链。
- **多 Agent 协作**：覆盖交易详情、地址角色识别、交易筛选、Trace 调试、补丁生成与补丁验证等阶段。
- **宏观 + 微观双阶段分析**：宏观阶段识别地址角色和交易类型，微观阶段围绕关键交易进行 Trace Debug 和补丁验证。
- **实时日志推送**：通过 WebSocket 推送任务状态、Agent 中间输出、工具调用和最终报告。
- **日志持久化与恢复**：PostgreSQL 保存任务与日志，Redis 缓存大段日志，`agents/logs/` 保留原始 Agent 文件日志。
- **可复现审计平台接口**：支持宏观分析结果、分页历史日志、完整日志、Agent 文件日志、归档和删除等 API。

## 技术栈

| 类型 | 技术 |
| --- | --- |
| Web API | FastAPI, Uvicorn |
| 实时通信 | WebSocket |
| 数据库 | PostgreSQL, SQLAlchemy |
| 缓存 | Redis |
| 大模型调用 | OpenAI-compatible API, DeepSeek/Qwen/GPT/MiMo 等 |
| 链上数据 | Tenderly, 区块链浏览器 API, Web3 |
| 容器化 | Docker, Docker Compose |

## 目录结构

```text
TracePilot-backend/
├── agents/                 # 多智能体实现与文件日志
├── app/                    # FastAPI 后端服务
│   ├── database/           # PostgreSQL/Redis 连接与 ORM 模型
│   ├── main.py             # REST API 与 WebSocket 入口
│   ├── models.py           # Pydantic 数据模型
│   ├── task_manager.py     # 任务生命周期与日志队列
│   └── websocket_manager.py
├── daos/                   # 链上数据访问封装
├── data/cache/             # 链上数据、源码、Trace 等缓存，不建议提交
├── dataset/                # 原始与处理后的 DApp 数据集
├── downloaders/            # 数据下载逻辑
├── entities/               # 合约、交易、Trace 等核心实体
├── md/                     # 汇报材料、产品规划、技术决策记录
├── process/                # 图分析、Trace 处理、调试模拟器
├── prompt/                 # LLM Prompt 模板
├── utils/                  # Token 管理、LLM 调用、通用工具
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── run_server.py
└── settings.py
```

## 环境要求

推荐使用 Docker 启动，避免本地 PostgreSQL/Redis 配置差异。

| 工具 | 推荐版本 |
| --- | --- |
| Docker Desktop / Docker Engine | 20.10+ |
| Docker Compose | v2+ |
| Python | 3.10+ |
| Git | 2.0+ |

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/Tlipped/TracePilot_backend.git
cd TracePilot_backend
```

### 2. 配置环境变量

复制示例配置：

```bash
cp .env_example .env
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env_example .env
```

至少需要检查：

```env
DATABASE_URL=postgresql://tracepilot_user:tracepilot_pass@postgres:5432/tracepilot
REDIS_URL=redis://redis:6379/0
PROJECT_PATH=/app

LLM_NAME=deepseek-reasoner
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MAX_CONCURRENT=5
```

还需要按你的链和数据源配置 Tenderly、区块链浏览器 API Key 等项目内配置项。不要把真实 API Key 提交到 GitHub。

### 3. 启动后端

```bash
docker-compose up --build
```

启动成功后应看到：

- `tracepilot_backend`
- `tracepilot_postgres`
- `tracepilot_redis`

### 4. 验证服务

打开：

- API 根地址：`http://localhost:8000`
- Swagger 文档：`http://localhost:8000/docs`

或执行：

```bash
curl http://localhost:8000/api/tasks
```

首次启动通常返回空数组。

## 前后端联调

后端启动后，再启动前端仓库：

```bash
cd ../TracePilot-dashboard
npm install
npm run dev
```

前端默认访问 `http://localhost:8000`。如需修改后端地址，在前端 `.env.local` 中配置：

```env
VITE_BACKEND_HTTP_URL=http://localhost:8000
VITE_BACKEND_WS_URL=ws://localhost:8000
```

然后访问：

```text
http://localhost:5173
```

## 常用 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/tasks` | 获取未归档任务列表 |
| `GET` | `/api/tasks?include_archived=true` | 获取全部任务，包括归档任务 |
| `POST` | `/api/tasks` | 创建分析任务 |
| `GET` | `/api/tasks/{task_id}` | 获取任务详情 |
| `GET` | `/api/tasks/{task_id}/macro-analysis` | 获取宏观分析结果，包括地址角色、交易分类、调试目标 |
| `GET` | `/api/tasks/{task_id}/logs` | 分页获取历史日志，支持 `limit` 和 `before_id` |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消运行中的任务 |
| `POST` | `/api/tasks/{task_id}/archive` | 归档已结束任务 |
| `POST` | `/api/tasks/{task_id}/unarchive` | 恢复归档任务 |
| `DELETE` | `/api/tasks/{task_id}` | 删除已结束任务和数据库日志 |
| `GET` | `/api/task/{task_id}/log/{log_id}` | 获取完整日志内容 |
| `GET` | `/api/tasks/{task_id}/agent-log-files` | 获取本地 Agent 日志文件列表 |
| `GET` | `/api/tasks/{task_id}/agent-log-files/{file_id}` | 读取指定 Agent 日志文件 |
| `WS` | `/ws/{task_id}` | 接收任务状态和 Agent 实时日志 |

## 任务生命周期

```text
Create -> Pending -> Running -> Completed / Failed
                              ├── Archive -> Restore
                              └── Delete
```

- **Cancel**：停止运行中的任务，并保留已有日志。
- **Archive**：不删除数据，只从首页默认列表隐藏，适合长期保留审计证据。
- **Restore**：将归档任务恢复到默认列表。
- **Delete**：删除任务记录和数据库日志，适合误建任务或无价值记录。

## 日志与数据存储

| 位置 | 内容 |
| --- | --- |
| Redis | 大段日志内容缓存，支持前端快速查看完整日志 |
| PostgreSQL `task_runs` | 任务状态、最终报告、归档状态 |
| PostgreSQL `task_logs` | 日志元数据、短日志、完整日志备份 |
| `agents/logs/` | 各 Agent 的原始文件日志 |
| `data/cache/` | 链上数据、合约源码、Trace、价格等缓存 |
| `dataset/processed/` | 宏观阶段处理结果，前端宏观分析面板会读取其摘要 |

`data/cache/` 和 `agents/logs/` 可能很大，通常不建议提交到 Git。

## Docker 常用命令

```bash
# 启动并构建
docker-compose up --build

# 后台启动
docker-compose up -d --build

# 查看后端日志
docker-compose logs -f backend

# 停止服务
docker-compose down

# 停止并删除数据库/Redis volume，谨慎使用
docker-compose down -v

# 强制重建
docker-compose up --build --force-recreate

# 查看容器资源占用
docker stats
```

## 本地 Python 启动

仅在不使用 Docker 时需要：

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run_server.py
```

本地启动前请保证：

- PostgreSQL 可访问，且 `DATABASE_URL` 配置正确。
- Redis 可访问，且 `REDIS_URL` 配置正确。
- `.env` 或 `settings.py` 中外部 API Key 已配置。

## 常见问题

### 1. 前端提示 `ERR_CONNECTION_REFUSED`

通常是后端没有启动，或前端后端地址配置错误。先确认：

```bash
docker-compose ps
curl http://localhost:8000/api/tasks
```

### 2. WebSocket 一直重连

先看后端日志：

```bash
docker-compose logs -f backend
```

常见原因包括模型 API、Tenderly、tiktoken 编码文件、网络访问或任务执行异常。

### 3. API Key 明明写了但容器里读不到

检查 `.env` 是否被 `docker-compose.yml` 加载，并重建容器：

```bash
docker-compose down
docker-compose up --build
```

也可以进入容器验证：

```bash
docker-compose exec backend python -c "import settings; print(settings.LLM_NAME, settings.LLM_BASE_URL, bool(settings.LLM_API_KEY))"
```

### 4. 数据库字段缺失或枚举错误

后端启动时会执行轻量 schema patch，自动补齐 `task_logs` 和 `task_runs` 的新增字段，并将日志级别字段转为字符串以兼容不同 Agent 输出。

### 5. GitHub 拒绝大文件推送

`data/cache/` 中可能有数百 MB 的 Trace 缓存，不应提交。请确认 `.gitignore` 已忽略缓存目录，并在提交前检查：

```bash
git status --short
```

## 开发建议

- 修改 API 后，同步更新前端 `src/services/api.ts` 和 `src/types/index.ts`。
- 大段日志、链上缓存和实验输出不要提交到仓库。
- 提交前建议至少运行：

```bash
python -m py_compile app\main.py app\task_manager.py app\models.py
```

如修改了前端，也在前端仓库运行：

```bash
npm run build
```

## 相关文档

- [工程化技术决策记录](./md/TracePilot工程化技术决策记录.md)
- [产品化需求分析与优化路线](./md/TracePilot产品化需求分析与优化路线.md)
- [前后端工作汇报](./md/汇报材料-前后端工作汇报.md)
