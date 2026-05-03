# TracePilot Backend

TracePilot 是一个面向区块链 DApp 漏洞定位的多智能体分析系统。系统会围绕跨交易攻击场景收集链上交易、执行 Trace、合约源码和状态变化，并通过多个 Agent 协作完成交易筛选、角色识别、故障定位、补丁生成和验证。

本仓库是 TracePilot 的后端服务，基于 FastAPI、WebSocket、PostgreSQL 和 Redis 构建，负责启动分析任务、推送实时日志、持久化任务记录，并向前端提供可复现的日志与报告接口。

论文背景：TracePilot: Self-verifiable Framework for Decentralized Applications Fault Localization across Transactions。

## 功能概览

- **跨交易漏洞定位**：分析多笔交易共同触发的 DApp 漏洞。
- **多 Agent 协作**：交易详情、角色识别、交易筛选、Trace Debug、补丁验证等 Agent 分工执行。
- **实时日志推送**：通过 WebSocket 将任务状态和 Agent 中间结果推送到前端。
- **日志持久化**：Redis 缓存大段日志，数据库保存任务与日志元数据，本地 `agents/logs` 保留 Agent 原始文件日志。
- **任务生命周期管理**：支持创建、取消、归档、恢复、删除任务。
- **可复现审计**：支持前端恢复历史任务、查看文件日志、导出报告和证据包。

## 技术栈

| 类型 | 技术 |
| --- | --- |
| Web API | FastAPI, Uvicorn |
| 实时通信 | WebSocket |
| 数据库 | PostgreSQL, SQLAlchemy |
| 缓存 | Redis |
| 大模型调用 | OpenAI-compatible API, DeepSeek/Qwen/GPT 等 |
| 链上数据 | Tenderly, Etherscan-like API, Web3 |
| 容器化 | Docker, Docker Compose |

## 目录结构

```text
TracePilot-backend/
├── agents/                 # 多智能体实现与文件日志
├── app/                    # FastAPI 后端服务
│   ├── database/           # PostgreSQL/Redis 连接与 ORM 模型
│   ├── main.py             # API 与 WebSocket 入口
│   ├── models.py           # Pydantic 数据模型
│   ├── task_manager.py     # 任务生命周期与日志队列
│   └── websocket_manager.py
├── benchmark/              # DApp 漏洞案例数据
├── daos/                   # 链上数据访问封装
├── data/cache/             # 链上数据、源码、Trace 等缓存
├── dataset/                # 处理后的数据集
├── downloaders/            # 数据下载逻辑
├── entities/               # 合约、交易、Trace 等核心实体
├── mcp_tools/              # 工具调用相关模块
├── process/                # Trace 处理、图分析、调试模拟器
├── prompt/                 # LLM Prompt 模板
├── utils/                  # Token 管理、LLM 调用、通用工具
├── docker-compose.yml      # 后端 + PostgreSQL + Redis
├── Dockerfile
├── main.py                 # 单案例/批处理入口
├── run_server.py           # FastAPI 启动脚本
├── settings.py             # 项目配置和 API Key
└── worker.py
```

## 环境要求

推荐使用 Docker 方式启动，能避免本地 PostgreSQL/Redis 配置差异。

| 工具 | 推荐版本 |
| --- | --- |
| Docker Desktop / Docker Engine | 20.10+ |
| Docker Compose | v2+ |
| Python | 3.10+ |
| Git | 2.0+ |

如果选择本地 Python 运行，还需要自行启动 PostgreSQL 和 Redis。

## 快速开始：Docker 启动后端

### 1. 克隆仓库

```bash
git clone https://github.com/Tlipped/TracePilot_backend.git
cd TracePilot_backend
```

如果你的目录名是 `TracePilot-backend` 也没有影响，下面命令在仓库根目录执行即可。

### 2. 配置 `settings.py`

后端运行前必须配置必要的外部服务 Key。请在 `settings.py` 中检查并填写：

- LLM API：模型名称、API Base URL、API Key。
- Tenderly API：用于获取链上交易模拟和 Trace。
- 区块链浏览器 API：如 Etherscan API Key，用于合约源码和交易数据。
- 其他项目内已有配置项：按你要分析的链和模型补齐。

不要把真实 API Key 提交到 GitHub。`settings.py` 建议只在本地维护。

### 3. 启动服务

```bash
docker-compose up --build
```

启动成功后应看到三个容器：

- `tracepilot_backend`
- `tracepilot_postgres`
- `tracepilot_redis`

### 4. 验证后端

打开：

- API 根地址：http://localhost:8000
- Swagger 文档：http://localhost:8000/docs

也可以执行：

```bash
curl http://localhost:8000/api/tasks
```

正常情况下会返回任务列表，首次启动通常是空数组。

## 前后端联调

后端启动后，再启动前端仓库：

```bash
cd ../TracePilot-dashboard
npm install
npm run dev
```

前端默认请求 `http://localhost:8000`。如需修改后端地址，可在前端 `.env.local` 中设置：

```env
VITE_API_BASE_URL=http://localhost:8000
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
| `POST` | `/api/tasks/{task_id}/cancel` | 取消运行中的任务 |
| `POST` | `/api/tasks/{task_id}/archive` | 归档已结束任务 |
| `POST` | `/api/tasks/{task_id}/unarchive` | 恢复归档任务 |
| `DELETE` | `/api/tasks/{task_id}` | 删除已结束任务及数据库日志 |
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
| Redis | 大段日志内容缓存，支撑前端快速查看完整日志 |
| PostgreSQL `task_runs` | 任务状态、最终报告、归档状态 |
| PostgreSQL `task_logs` | 日志元数据、短日志、完整日志备份 |
| `agents/logs/` | 各 Agent 的原始文件日志 |
| `data/cache/` | 链上数据、合约源码、Trace、价格等缓存 |

`data/cache/` 和 `agents/logs/` 可能很大，通常不建议提交到 Git。

## Docker 常用命令

```bash
# 启动并构建
docker-compose up --build

# 后台启动
docker-compose up -d --build

# 查看日志
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

## 本地 Python 启动方式

仅在你不使用 Docker 时需要。

```bash
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # macOS/Linux

pip install -r requirements.txt
python run_server.py
```

本地启动前请保证：

- PostgreSQL 可访问，且 `DATABASE_URL` 配置正确。
- Redis 可访问，且 `REDIS_URL` 配置正确。
- `settings.py` 中 API Key 已配置。

## 常见问题

### 1. 前端提示 `ERR_CONNECTION_REFUSED`

通常是后端没有启动，或前端的 `VITE_API_BASE_URL` 指向了错误端口。先确认：

```bash
docker-compose ps
curl http://localhost:8000/api/tasks
```

### 2. WebSocket 一直重连

先看后端日志：

```bash
docker-compose logs -f backend
```

常见原因是任务执行过程中模型 API、Tenderly、tiktoken 编码文件或网络访问失败。

### 3. `tiktoken` 下载编码文件失败

容器网络无法访问 OpenAI public blob 时可能出现。当前代码已有 fallback，但如果仍失败，建议检查 Docker DNS 或提前缓存依赖。

### 4. 归档任务不见了

这是预期行为。默认 `/api/tasks` 不返回归档任务。前端切换到 `Archived` 或 `All records` 即可查看。

### 5. 数据库字段缺失

后端启动时会执行轻量 schema patch，自动补齐 `task_logs` 和 `task_runs` 的新增字段。若仍报错，可重启后端容器或检查 PostgreSQL 权限。

## 开发建议

- 修改 API 后同步更新前端 `src/services/api.ts` 和 `src/types/index.ts`。
- 大段日志和缓存不要提交到仓库。
- 提交前建议至少运行：

```bash
python -m py_compile app\main.py app\task_manager.py app\models.py
```

如修改了前端，也在前端仓库运行：

```bash
npm run lint
npm run build
```
