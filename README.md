# TracePilot

TracePilot 是一个用于跨交易定位去中心化应用程序（DApp）故障的自验证框架。它能够分析智能合约交易，定位潜在的漏洞，并生成详细的问题报告。该项目基于论文《TracePilot: Self-verifiable Framework for Decentralized Applications Fault Localization across Transactions》实现。

## 目录

- 概述
- 核心功能
- 安装要求
- 快速开始
- 项目结构
- Docker 部署指南
- Docker 管理命令

------

## 概述

### 核心功能

- **跨交易故障定位**：追踪并定位跨多笔交易的 DApp 故障。
- **智能合约分析**：深入分析合约逻辑与执行路径。
- **自动化漏洞检测**：识别常见与复杂的智能合约漏洞。
- **详细故障报告生成**：输出结构化的分析报告与修复建议。
- **多区块链网络支持**：适配主流 EVM 兼容链。

------

## 安装要求

### 系统要求

| 项目     | 最低配置                          | 推荐配置   |
| :------- | :-------------------------------- | :--------- |
| 操作系统 | Windows 7+ / macOS 10.12+ / Linux | 最新稳定版 |
| 内存     | 8GB                               | 16GB+      |
| 存储空间 | 10GB                              | 20GB+      |
| 架构     | x86_64 或 ARM64                   | -          |

### 软件要求

- **Python**: 3.9+
- **Node.js**: 20.9
- **Docker**: 20.10+（可选）
- **Docker Compose**: v2.0+（可选）
- **Git**: 2.0+

------

## 快速开始

### 使用 Docker（推荐）

这是最快速、最一致的部署方式：

1. **克隆仓库**

   bash

   ```
   git clone https://github.com/Tlipped/TracePilot-backend.git
   cd TracePilot-backend
   ```

   

2. **配置环境变量**

   编辑 `settings.py`，配置以下信息：

   - Tenderly API Key（区块链仿真）
   - LLM 配置（模型名称、API 端点、API 密钥）
   - 区块链 API 密钥（如 Etherscan API Key）

3. **构建并启动服务**

   bash

   ```
   docker-compose up --build
   ```

   

4. **访问应用**

   - 应用接口：`http://localhost:8000`
   - API 文档：`http://localhost:8000/docs`

------

## 项目结构

text

```
TracePilot/
├── agents/                 # 智能代理模块
│   ├── AgentBase.py        # 代理基类
│   ├── FilterAgent.py      # 信息筛选代理
│   ├── FixAgent.py         # 修复建议代理
│   ├── GlobalMemoryAgent.py # 全局记忆代理
│   ├── JudgeAgent.py       # 结果评估代理
│   ├── TaskAgent.py        # 任务协调代理
│   ├── TraceAgent.py       # 交易追踪代理
│   ├── TxDetailAgent.py    # 交易详情代理
│   ├── TxFaultAgent.py     # 故障识别代理
│   ├── TxRoleAgent.py      # 角色分析代理
│   └── __init__.py
├── app/                    # 主应用（FastAPI）
│   ├── database/           # 数据库模块
│   │   ├── models.py       # ORM 模型
│   │   └── redis_client.py # Redis 客户端
│   ├── main.py             # 应用入口
│   ├── models.py           # API 数据模型
│   ├── task_manager.py     # 任务管理
│   ├── websocket_manager.py # WebSocket 管理
│   └── utils.py            # 应用工具函数
├── benchmark/              # 149 个 DApp 故障案例数据集
├── daos/                   # 数据访问对象
│   ├── contract.py         # 合约数据访问
│   ├── tenderly.py         # Tenderly API
│   ├── trace.py            # 追踪数据访问
│   └── tx.py               # 交易数据访问
├── data/cache/             # 缓存数据（字节码、源码、价格等）
├── downloaders/            # 区块链数据下载器
├── entities/               # 核心实体定义（合约、交易、追踪）
├── mcp_tools/              # 模型上下文协议工具
├── misc/                   # 辅助脚本（AST、编译器、追踪器）
├── process/                # 核心处理逻辑
│   ├── trace/              # 追踪处理与调试模拟
│   ├── analyze.py          # 分析主逻辑
│   └── fund_graph.py       # 资金流向图
├── prompt/                 # LLM 提示模板
├── utils/                  # 通用工具库
├── experiment_result/      # 实验结果与报告
├── Dockerfile
├── docker-compose.yml
├── settings.py             # 项目配置
├── main.py                 # 单案例处理入口
├── run_server.py           # 服务启动脚本
└── worker.py               # 并发执行启动器
```



------

## Docker 部署指南

### 系统要求

- Docker Desktop（Windows / Mac）或 Docker Engine（Linux）
- 分配至少 **4GB 内存** 给 Docker
- 至少 **8GB 可用磁盘空间**

### 配置 Docker 引擎

在 Docker Desktop 的 `Settings → Docker Engine` 中，使用以下配置（国内镜像加速）：

json

```
{
  "builder": {
    "gc": {
      "defaultKeepStorage": "20GB",
      "enabled": true
    }
  },
  "dns": ["8.8.8.8", "8.8.4.4"],
  "features": {
    "buildkit": true
  },
  "registry-mirrors": [
    "https://registry-1.docker.io",
    "https://docker.1ms.run",
    "https://docker-0.unsee.tech",
    "https://docker.m.daocloud.io"
  ]
}
```



### 构建并启动服务

bash

```
docker-compose up --build
```



启动后应看到以下三个服务正常运行：

- `tracepilot_backend`
- `tracepilot_postgres`
- `tracepilot_redis`

------

## Docker 管理命令

bash

```
# 查看实时日志
docker-compose logs -f

# 停止所有服务
docker-compose down

# 强制重新构建并启动
docker-compose up --build --force-recreate

# 查看资源占用
docker stats

# 清理未使用的 Docker 资源
docker system prune -f
```