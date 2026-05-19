# TracePilot 架构说明与面试准备

> 面试定位：TracePilot 是一个面向区块链 DApp 资产安全的多智能体自动化漏洞定位系统。它把链上证据、交易 Trace、合约源码、状态变化和补丁验证串成闭环，用大模型完成跨交易攻击场景下的根因定位、补丁生成与验证，并通过 WebSocket + 日志持久化把科研原型工程化为可交互、可复现的平台。

## 1. 项目目标

TracePilot 解决的问题不是单次代码审计，而是跨交易攻击下的 DApp 漏洞定位。真实攻击往往由多笔交易共同构成：初始化、资金准备、攻击触发、获利转移、辅助验证等交易交织在一起，每笔交易又包含很深的函数调用树。人工审计需要反复查看链上数据、源码、执行流、状态差异和事件日志，成本高且难复现。

系统目标可以概括为三点：

- 自动化：用多 Agent 分工完成交易筛选、角色识别、Trace 调试、补丁生成和验证。
- 可控性：用工具调用获取确定性链上证据，减少模型凭空推断。
- 可复现：把任务状态、Agent 输出、完整日志、最终报告和文件日志持久化，前端可恢复和导出。

## 2. 总体架构

```text
User / Interview Demo
        |
        v
TracePilot Dashboard (React + TypeScript + Ant Design)
        |
        | REST: task CRUD / logs / reports
        | WebSocket: task status + Agent events
        v
TracePilot Backend (FastAPI)
        |
        | TaskManager: task lifecycle, queue, cancellation, persistence
        v
Workflow Thread
        |
        +--> DAppProcess: chain data collection and preprocessing
        |       |
        |       +--> Tenderly / RPC / Etherscan-like APIs
        |       +--> Fund flow graph / transaction roles / trace initialization
        |
        +--> DAppAnalyze: multi-agent debug-patch-verify loop
                |
                +--> LLMClient: OpenAI-compatible model API
                +--> MCP Tools: precise trace/source/state retrieval
                +--> Patch replay and judge

Persistence Layer
        |
        +--> PostgreSQL: task_runs, task_logs metadata and final reports
        +--> Redis: full log content by log_id
        +--> agents/logs: per-Agent raw file logs
        +--> data/cache: chain data, source code, trace and summaries
```

## 3. 技术栈

| 层次 | 技术 | 作用 |
| --- | --- | --- |
| 前端 | React 18, TypeScript, Vite, Ant Design, lucide-react | 任务工作台、实时日志、报告渲染、导出 |
| 通信 | REST API, WebSocket | 任务管理和实时事件推送 |
| 后端 | FastAPI, asyncio, ThreadPoolExecutor | Web API、任务生命周期、异步任务协调 |
| 模型调用 | OpenAI-compatible API, DeepSeek/Qwen/GPT/MiMo 等 | 多 Agent 推理、函数调用、补丁生成 |
| 工具增强 | FastMCP, 自定义 Trace 调试工具 | 源码、执行流、状态快照、事件日志按需查询 |
| 数据源 | Tenderly, JSON-RPC, Etherscan-like API | 链上交易、Trace、合约源码、事件和状态 |
| 存储 | PostgreSQL, Redis, local files | 任务、日志、完整内容、原始 Agent 日志 |
| 容器化 | Docker Compose | 后端、PostgreSQL、Redis 一键启动 |

## 4. 前后端边界

后端仓库：`F:\keyan_learning\TracePilot-backend`

- `app/main.py`：FastAPI 入口，提供任务 API、WebSocket、日志文件读取接口。
- `app/task_manager.py`：任务创建、启动、取消、归档、删除、恢复、持久化和事件队列。
- `agents/`：多智能体实现。
- `process/pyg.py`：DApp 数据预处理和交易级分析准备。
- `process/analyze.py`：Debug-Patch-Verify 主循环。
- `mcp_tools/mcp_server.py`：Trace 调试工具集。
- `utils/agent_helpers.py`：Token 管理、日志桥接、响应解析。
- `utils/llm_client.py`：异步模型客户端和并发控制。

前端仓库：`F:\keyan_learning\TracePilot-dashboard`

- `src/pages/TaskList.tsx`：任务首页，支持创建、查看、取消、归档、恢复、删除。
- `src/pages/Dashboard.tsx`：任务详情工作台，聚合 Agent 状态、日志流、时间线、报告和文件日志。
- `src/services/api.ts`：REST API 封装。
- `src/services/WebSocketService.ts`：WebSocket 连接、重连、事件归一化、历史缓存。
- `src/components/StructuredReport.tsx`：最终报告结构化展示和 Markdown/JSON 导出。
- `src/config/appConfig.ts`：后端 HTTP/WS 地址统一配置，便于上线部署。

## 5. 端到端数据流

### 5.1 任务创建与启动

1. 用户在前端选择 DApp，调用 `POST /api/tasks`。
2. 后端 `TaskManager.create_task` 生成 `task_id`，写入 `task_runs`，创建任务事件队列。
3. `TaskManager.start_task` 将工作流放入 `ThreadPoolExecutor`，避免长任务阻塞 FastAPI 主事件循环。
4. 前端跳转到任务详情页，建立 `WebSocket /ws/{task_id}`。

### 5.2 链上数据采集与预处理

`DAppProcess.process` 负责把原始 DApp case 转成模型可分析的结构化上下文：

1. `TxDetailAgent` 获取交易详情、Trace、合约、事件和状态变化。
2. `FundFlowGraphBuilder` 构建资金流图，辅助识别获利方向。
3. `TraceAgent.init` 通过 MCP 初始化每笔交易的 Trace 调试会话。
4. `TxRoleAgent` 判断交易角色，例如攻击交易、辅助交易、受益人相关交易。
5. `Transaction Filter` 去重相似攻击模板，减少重复分析。
6. `TxFaultAgent` 生成交易级异常摘要，给后续 Trace Debug 提供宏观线索。

这一阶段的输出是 `processed_data`，包含交易详情、交易角色、资金变化、Trace 初始树、待分析交易列表等。

### 5.3 多 Agent Debug-Patch-Verify 闭环

`DAppAnalyze.analyze` 是核心闭环：

```text
GlobalMemory 初始化
        |
        v
Task Organizer 生成当前调试计划
        |
        v
Transaction Debugger 调用工具定位漏洞函数
        |
        +--> update_understanding: 产生新洞察
        +--> switch_transaction: 跨交易切换
        +--> ready_for_patch: 进入补丁阶段
        |
        v
Code Patcher 生成 Solidity 补丁并重放验证
        |
        v
Transaction Judge 判断补丁是否真正抵抗攻击
        |
        +--> VERIFIED: 输出最终报告
        +--> WRONG_ROOT_CAUSE: 回到 Debugger 重新定位
        +--> INEFFECTIVE_PATCH / BROKEN_LOGIC: 修改补丁继续验证
```

这个闭环的核心价值是“自验证”：模型不是只输出一个看似合理的漏洞解释，而是必须生成可落地补丁，并通过重放攻击验证补丁是否有效。

## 6. Agent 职责边界

| Agent | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| TxDetailAgent | 汇总每笔交易详情 | 原始交易哈希、链上 API | 交易摘要、Trace、事件、状态变化 |
| TxRoleAgent | 识别交易角色 | 资金流、交易详情、地址属性 | 攻击交易、辅助交易、受益人线索 |
| Transaction Filter | 去重和筛选攻击交易 | 攻击交易候选集合 | 待重点分析交易 |
| TxFaultAgent | 宏观异常归纳 | 交易详情、角色、资金流 | DApp 故障摘要 |
| Task Organizer | 组织当前分析任务树 | 全局记忆、验证反馈 | 下一步调试计划 |
| Transaction Debugger | Trace 级漏洞定位 | 全局记忆、Trace 树、工具结果 | 漏洞假设、根因函数、补丁方向 |
| GlobalMemory Administrator | 维护跨轮次共识 | 各 Agent 新洞察 | 当前确定事实、疑点、待验证项 |
| Code Patcher | 生成并应用补丁 | 漏洞假设、源码、编译信息 | Solidity 补丁、重放日志 |
| Transaction Judge | 判断补丁质量 | 真实资金变化、重放日志、补丁 | VERIFIED 或反馈原因 |

面试讲法：不是为了“堆 Agent”，而是因为这个任务天然有多种认知角色。交易角色识别、Trace 调试、补丁生成和验证需要不同上下文、不同输出格式和不同工具权限。拆成多 Agent 后，职责边界更清楚，日志更可观测，也方便针对某一阶段失败做回滚和重试。

## 7. 上下文管理与剪枝策略

### 7.1 为什么长上下文仍然要剪枝

即使模型支持很长窗口，跨交易攻击的 Trace 仍可能呈指数级膨胀。一个 DApp case 可能包含多笔交易，每笔交易都有深层函数调用树、内部调用、外部合约调用、事件、storage 读写、源码和历史对话。直接把所有证据塞进 Prompt 会带来三个问题：

- 成本高：Token 消耗大，推理延迟高。
- 噪声高：大量无关辅助交易和低价值调用会稀释关键证据。
- 不稳定：多轮工具调用和失败重试会不断累积历史，容易撑爆上下文或导致结论漂移。

### 7.2 Token budget 设计

`TokenManager` 根据 `MODEL_CONTEXT_WINDOWS` 和 `MODEL_MAX_OUTPUT_TOKENS` 计算可用预算。`TraceAgent._judge_too_long_trace` 会把上下文窗口拆成：

```text
可用 Trace 预算 =
模型上下文上限
- 预留最大输出
- safety buffer
- 当前历史对话 token
- 静态 prompt token
```

这样做的好处是：不是固定截断，而是根据模型窗口、历史长度和当前任务动态分配 Trace 空间。

### 7.3 语义感知剪枝

Trace 不是普通文本，而是函数调用树。系统通过 `DebugSimulator` 维护每个节点的展开/折叠状态：

- 初始只展开有限深度，降低整体噪声。
- Agent 可以调用 `expand_node` 深入可疑子树。
- 对低价值子树调用 `collapse_node` 折叠。
- 对已经分析过的子树通过 `update_comments` 保留摘要和发现。
- 对明显低价值节点进行规则过滤，例如 precompile 地址、低 gas 的 STATICCALL。

因此剪枝不是简单从头截断，而是“保留关键路径 + 折叠冗余子树 + 用注释承载已分析信息”。简历里写的 30K 到 12K 可以表述为案例级结果：在 SushiSwap 等复杂案例中，通过动态剪枝将单案例输入规模从约 30K 降到约 12K，同时保持根因定位和补丁验证效果。

## 8. 工具增强与 RAG 的关系

TracePilot 更准确地说是 Tool-Augmented Agent，而不是传统向量库 RAG。

传统 RAG 通常是：

```text
query -> vector search -> retrieve chunks -> prompt
```

TracePilot 的证据检索是：

```text
Trace node index / tx hash -> deterministic tool call -> source/state/event/flow evidence
```

当前更适合工具调用而不是向量数据库的原因：

- 链上证据需要精确定位，不能依赖语义相似度猜测。
- Trace 节点有明确 index，可以直接取源码、内部执行流、状态快照和事件日志。
- 审计场景要求证据可追溯，工具调用返回的是确定性数据。

向量数据库可以作为后续扩展，用于漏洞知识库、历史审计报告、常见修复模式检索，但不适合替代链上证据检索。

## 9. 工程化与可观测性

### 9.1 WebSocket 实时推送

后端在 `app/main.py` 中提供 `WebSocket /ws/{task_id}`。任务运行时，`AgentLogger` 将 Agent 输出转换成 `LogMessage`，`TaskManager` 写入任务队列，WebSocket sender 再推给前端。

为了应对长日志流，系统做了：

- outbound queue：避免 WebSocket 发送阻塞任务执行。
- backpressure drop notice：日志过多时丢弃部分普通日志，但保留控制事件。
- heartbeat：检测连接状态。
- reconnect：前端断线后自动重连。
- persisted replay：任务完成后重新进入页面，可以从数据库加载历史日志。

### 9.2 Redis + PostgreSQL + 文件日志

| 存储 | 保存内容 | 设计原因 |
| --- | --- | --- |
| PostgreSQL `task_runs` | 任务状态、DApp 名称、最终报告、归档状态 | 支撑首页列表、刷新恢复、生命周期管理 |
| PostgreSQL `task_logs` | log_id、agent、level、message_type、摘要、时间戳 | 支撑检索、筛选、详情索引 |
| Redis | 完整日志内容 | 大段 Markdown/JSON 读取快，避免数据库频繁传输超长文本 |
| `agents/logs/` | 每个 Agent 的原始文件日志 | 支撑审计复盘和离线排查 |
| `data/cache/` | 链上数据、源码、Trace、交易摘要 | 避免重复请求 Tenderly/RPC，降低成本 |

面试讲法：这套日志系统不是“把 print 传到前端”，而是把 Agent 运行过程变成可检索、可恢复、可导出的证据链。

## 10. 前端工作台设计

前端不是简单渲染 Markdown，而是把多 Agent 长链推理过程拆成几个可操作视图：

- Task List：任务创建、状态筛选、取消、归档、恢复、删除。
- Agent State：9 个 Agent 的事件数、错误数、最新状态。
- Log Stream：滚动日志流，支持按 Agent、level、message type 过滤。
- Agent Timeline：把工具调用、结果、告警、任务状态整理成时间线。
- Log Detail Drawer：点击日志查看完整 Markdown/JSON。
- File Logs：读取后端 `agents/logs/{time}/{DApp}` 下的原始 Agent 日志。
- Structured Report：把最终报告拆成漏洞根因、攻击路径、关键交易、补丁建议、验证结果，并支持导出 Markdown/JSON evidence package。

这部分可以对 JD 中的“工程落地”和“应用开发能力”展开：前端让科研系统从脚本输出变成了可交互、可观测、可复盘的平台。

## 11. 核心创新点

### 11.1 跨交易漏洞定位

传统漏洞定位往往以单笔交易或静态代码为中心。TracePilot 把多笔交易的资金流、角色、Trace 和状态变化统一建模，适合治理攻击、价格操纵、权限滥用、跨交易准备-触发-获利链条等场景。

### 11.2 语义感知动态剪枝

系统不是粗暴截断 Trace，而是结合调用树结构、节点类型、交易角色、受益人线索、Agent 当前任务目标来动态展开或折叠。它在 Token 预算内尽量保留关键路径，同时压缩重复和低价值上下文。

### 11.3 工具增强的可证据推理

Agent 不依赖记忆或猜测，而是通过 MCP 工具按需读取：

- 当前 Trace 树
- 函数源码
- 合约源码
- 内部执行流
- 状态快照
- storage 变化
- event logs
- patch 所需编译信息

这降低了幻觉，也让最终结论有证据链支撑。

### 11.4 Debug-Patch-Verify 自验证闭环

定位结果必须经补丁和攻击重放验证。Judge 会判断：

- 补丁是否抵抗攻击。
- 是否修错根因。
- 是否破坏原有业务逻辑。
- 是否只是补丁实现失败而不是假设错误。

这比只输出漏洞解释更接近真实审计流程。

### 11.5 可复现平台化

通过 FastAPI、WebSocket、Redis、PostgreSQL、文件日志和前端 Dashboard，系统支持任务恢复、日志查看、报告导出和证据包保存，具备从论文原型走向工程演示的能力。

## 12. 评估与实验表达

简历中可以这样表述：

- 数据集：参与构建 149 例真实 DApp 漏洞数据集，包含跨交易攻击案例。
- 基线：复现 DAppFL，保证对比实验公平。
- 定位指标：使用 Recall@Top-1、Precision 等评估根因函数定位效果。
- 验证指标：使用补丁验证率衡量系统输出是否能抵抗重放攻击。
- Token 效率：在复杂案例中，通过上下文剪枝将单案例输入从约 30K 降到约 12K，降低约 60%。
- 模型敏感性：对 DeepSeek、GPT、Qwen、Llama、MiMo 等 OpenAI-compatible 模型做过接入或对比，说明系统依赖一定推理能力，但工程层支持多模型切换。

面试时要避免说“完全没有损失”。更稳的说法是：在多数案例中剪枝后仍能保留核心证据并维持可验证定位效果；对于极复杂跨交易案例，过度压缩仍可能影响定位质量，因此系统保留了工具展开、注释和失败回滚机制。

## 13. 与华为终端 BG 软件部的对应关系

| 终端 BG 软件部方向 | TracePilot 对应能力 |
| --- | --- |
| HarmonyOS / 操作系统软件 | 任务生命周期、异步执行、日志可观测、状态恢复和工程稳定性设计 |
| AI 技术应用 | 多 Agent 编排、OpenAI-compatible API、function calling/MCP 工具调用 |
| 大模型上下文管理 | Token budget、长上下文剪枝、历史压缩、模型窗口差异适配 |
| 安全可信 | DApp 漏洞定位、攻击重放验证、补丁验证、storage slot 风险识别 |
| 分布式协同 | WebSocket 实时事件、Redis/PostgreSQL 持久化、任务恢复、前后端状态同步 |
| 开发者生态 / 工程平台 | 将科研原型做成可交互、可复盘、可导出的分析平台 |
| AI 算法/图结构理解 | 资金流图、Trace 调用树、跨交易关系建模 |
| 编程能力 | Python async、FastAPI、SQLAlchemy、Redis、TypeScript、React |

## 14. 面试高频问题回答模板

### 为什么不用一个大 Prompt？

一个大 Prompt 会把交易筛选、Trace 调试、补丁生成、验证判断混在一起，难以控制上下文和错误回滚。TracePilot 把流程拆成多个 Agent，每个 Agent 有清晰职责、输入输出和工具权限；失败时可以定位到具体阶段，例如 Trace 假设错了就回到 Debugger，补丁实现错了就让 Patcher 重试。

### 你们是不是 RAG？

不是传统向量库 RAG，而是工具增强 Agent。因为链上审计需要精确证据，例如某个 Trace 节点的源码、storage 变化和 event logs。我们通过节点 index 和交易 hash 精确调用工具获取证据，比语义相似检索更可靠。向量库更适合后续扩展漏洞知识库和历史审计报告检索。

### 长上下文模型为什么还要剪枝？

长上下文不等于无限上下文。跨交易 Trace 会快速膨胀，而且模型注意力和推理质量会被噪声影响。剪枝可以降低成本、减少噪声、保留关键路径，并给输出和后续工具调用留足预算。

### 如何避免多 Agent 结论漂移？

系统用 GlobalMemory 维护当前确定事实、待验证假设和未解决问题；Judge 的补丁验证结果会反向约束 Debugger。如果补丁无法抵抗攻击，系统不会直接接受原假设，而是把失败原因写回全局记忆，重新定位根因。

### WebSocket 做了什么工程价值？

长链 Agent 任务可能运行很久，如果只有最终结果，用户无法知道系统卡在哪。WebSocket 把任务状态、工具调用、中间推理和最终报告实时推送到前端；同时日志写入 Redis/数据库和本地文件，刷新后还能恢复上下文，支撑可复现审计。

### 你在项目中的核心贡献怎么讲？

我主要做了两块：一是上下文剪枝和 Token 管理，围绕 Trace 调用树设计动态预算和展开/折叠机制；二是工程落地，把后端任务、WebSocket 实时推送、Redis/数据库日志持久化、前端可视化工作台串起来，让论文原型变成可交互、可复盘、可导出的系统。

## 15. 可以继续优化的方向

- 将 `settings.py` 中的密钥迁移到 `.env`，避免误提交。
- 为 Agent 输出定义更强的 JSON schema，降低解析失败率。
- 引入漏洞知识库 RAG，用于补丁模式推荐和历史案例对比。
- 对 WebSocket 日志增加分页和服务端游标，降低超长任务前端内存压力。
- 对 Trace 剪枝增加可量化指标，例如保留关键节点比例、有效工具调用命中率、压缩后定位成功率。
- 对模型敏感性实验形成标准 RQ，比较不同模型在定位、补丁和成本上的表现。

## 16. 华为终端 BG 软件部面试口径

TracePilot 不是 HarmonyOS 项目，面试时不要硬包装。更稳的讲法是：它体现的是“AI 技术应用 + 安全可信 + 工程平台化”的能力，这些能力可以迁移到终端 BG 软件部关注的 HarmonyOS、终端 AI、安全、分布式协同和开发者生态方向。

推荐表达：

> TracePilot 的场景是区块链安全，但核心能力是可迁移的。它训练了我在多 Agent 编排、工具调用、上下文管理、漏洞验证、异步任务、日志可观测和状态持久化上的能力。终端 BG 软件部做 HarmonyOS 和智能终端软件，同样需要稳定工程、安全可信、AI 技术应用和可观测性，所以我会把这个项目作为 AI+安全+工程落地能力的证明，而不是把它说成终端系统项目。

### 16.1 和 HarmonyOS 的连接点

- HarmonyOS 强调系统能力、稳定性和用户体验；TracePilot 中任务生命周期、日志恢复、WebSocket 推送和失败回滚体现了复杂软件系统的稳定性意识。
- HarmonyOS 面向全场景设备协同；TracePilot 中前端工作台、后端任务系统、Redis/PostgreSQL、文件日志之间的状态同步，可以迁移到分布式状态管理和协同体验设计。
- HarmonyOS 生态需要开发者工具；TracePilot 的 Dashboard、结构化报告、日志检索和证据包导出，本质上是一类开发者/分析者工具平台。
- 终端安全要求可信和可验证；TracePilot 的补丁验证、攻击重放和 Judge 反馈闭环，体现了安全分析中“不能只靠模型说法，要有验证”的思路。

### 16.2 和 AI 技术应用的连接点

- 多 Agent 不是堆概念，而是把复杂任务拆成交易分析、Trace 调试、补丁生成、验证判断等明确角色。
- 工具调用不是普通 RAG，而是面向确定性证据的精确检索，适合安全、系统诊断、日志分析等场景。
- 上下文剪枝解决的是模型输入噪声和成本问题，可以迁移到终端 AI 助手、系统日志分析、开发者问答等长上下文场景。
- Debug-Patch-Verify 闭环对应 AI 应用里的结果校验和自反馈机制，避免只生成不可验证结论。

## 17. “最大挑战是什么，怎么解决”的回答模板

面试官问这个问题时，优先讲 TracePilot，因为它更贴合 AI 技术应用、安全可信和工程验证闭环。

### 17.1 首选回答：让模型输出可验证，而不是只看起来合理

短答版：

> TracePilot 最大的挑战是如何让大模型在漏洞定位场景下输出可验证结果。跨交易攻击的 Trace 很长，单案例可能超过 30K token，直接给模型成本高、噪声大，所以我设计了语义感知动态剪枝，把输入压缩到约 12K。另一方面，模型生成 Solidity 补丁后，还会遇到 storage slot 冲突、Solidity 版本差异、代理合约 constructor 和 initializer 不一致等真实工程问题。我们通过工具调用、补丁编译、攻击重放和 Judge 反馈，把模型输出放回真实环境验证，区分编译失败、初始化失败、补丁无效、根因错误和业务逻辑破坏。这个过程让我认识到，AI 应用开发不能只看模型回答，还要有上下文管理、工具增强、验证闭环和工程可观测性。

展开版：

> 一开始系统能让模型输出漏洞解释，但安全场景只输出解释是不够的。模型可能说得很合理，但补丁落到 Solidity 工程里会遇到很多现实问题。例如代理合约有固定 storage layout，模型随意新增状态变量可能造成 storage slot 冲突；不同 DApp 使用的 Solidity 版本不同，模型生成的新语法不一定兼容；可升级合约通常依赖 initializer，constructor 在代理上下文下不会按预期执行。
>
> 我的处理思路是把验证链路拆细。第一，在上下文侧通过 Trace 剪枝降低噪声，让模型聚焦关键调用路径。第二，在工具侧让 Agent 按需读取源码、Trace、状态变化和编译信息，减少幻觉。第三，在补丁侧把失败类型细分为 compile error、patch apply error、initialization error、attack still succeeds、business logic broken。不同失败类型回写给不同 Agent：编译和初始化问题回给 Patcher，攻击仍成功可能回给 Debugger，业务逻辑破坏则由 Judge 要求更保守的修复。
>
> 这个挑战和终端软件也有相似性：终端系统里的 AI 能力不能只生成建议，还要能回到系统日志、权限、设备状态或测试结果中验证，才能真正用于产品。

### 17.2 补丁验证挑战：storage slot 冲突

面试官追问时可以这样说：

> EVM 合约的状态变量会按顺序映射到底层 storage slot。升级合约或代理合约场景下，如果新实现合约改变变量顺序或插入新变量，就可能导致同一个 slot 被解释成不同变量。比如原来 slot 0 是 owner，新补丁把 slot 0 当成 locked，读写就会错位。我们的处理是约束模型不要随意新增 storage，优先使用已有变量、modifier 或局部逻辑修复，并通过攻击重放和状态检查判断补丁是否破坏原逻辑。

### 17.3 补丁验证挑战：Solidity 版本和初始化差异

面试官追问时可以这样说：

> 不同 DApp 的 Solidity 版本和依赖库差异很大。模型可能生成新版本语法，但原项目是旧编译器；或者模型用 constructor 初始化变量，但代理合约实际需要 initializer。我们把编译版本、合约继承关系、初始化函数、依赖库信息放进 Patcher 上下文，并让 Judge 区分编译失败、初始化失败、补丁无效和根因错误，从而更准确地回滚到对应阶段。

### 17.4 长 Trace 上下文剪枝挑战

短答版：

> 长 Trace 剪枝的难点是不能简单截断。Trace 是函数调用树，关键证据可能藏在很深的内部调用里，而前面大量内容可能只是低价值查询。我的做法是基于 Trace 树结构做语义感知剪枝：初始只展开有限深度，对低价值子树折叠，对可疑节点按需展开，对已分析节点用注释保留摘要，并根据模型窗口、历史对话和最大输出动态计算 token budget。这样把单案例输入从约 30K 降到 12K，同时尽量保留根因定位证据。

## 18. 终端 BG 面试高频问题

### Q1：你这个项目和 HarmonyOS 有什么关系？

> 它不是 HarmonyOS 项目，但它体现了终端软件研发需要的几类能力：复杂任务拆解、异步执行、状态恢复、日志可观测、安全验证和 AI 技术应用。如果放到 HarmonyOS 场景，可以迁移到系统诊断、开发者工具、日志分析、端侧 AI 助手或安全分析平台。

### Q2：如果让你做 HarmonyOS 上的 AI 助手或系统诊断功能，你会怎么设计？

> 我会分四层：端侧交互与权限层、系统能力调用层、AI 推理或规则判断层、日志评测与安全审计层。端侧要保证低延迟和隐私边界；系统能力调用层负责获取设备状态、日志和 API 结果；AI 层负责分析和生成解释；最后要用结构化日志和测试结果验证输出，避免模型凭空判断。

### Q3：你怎么看端侧 AI 和云侧 AI 的分工？

> 端侧适合低延迟、隐私敏感和轻量推理，例如唤醒、分类、缓存、权限判断和初步意图识别。云侧适合复杂推理、大模型、多模态和大规模知识检索。终端软件里通常不是二选一，而是端云协同：端侧做隐私保护和快速响应，云侧做复杂推理，最终回到端侧形成自然体验。

### Q4：你如何保证 AI 系统稳定可靠？

> 我会从四方面做：第一，输入侧做权限、格式和上下文预算控制；第二，推理侧使用工具调用或检索提供可信证据；第三，输出侧用 schema、规则或测试结果校验；第四，工程侧做日志、监控、失败重试、降级和可复盘。TracePilot 里的 Judge、WebSocket 日志、Redis/数据库持久化就是类似思路。

### Q5：你的不足是什么？

> 我目前对 HarmonyOS/OpenHarmony 的系统机制还在补强，项目经历更多集中在 Python 后端、AI 工程化和安全分析。但我的优势是学习和落地速度比较快，已经做过多 Agent、异步任务、日志可观测和验证闭环。后续我会重点补 OpenHarmony 架构、分布式软总线、ArkTS/应用模型、端侧性能和安全机制。

## 19. 面试中可以主动抛出的亮点

- TracePilot 不是简单调用大模型，而是做了工具增强、多 Agent 编排、上下文剪枝和补丁验证闭环。
- 我关注的是 AI 结果能不能被真实环境验证，而不是只生成看似合理的文本。
- 我把科研原型做成了可交互、可恢复、可导出、可复盘的平台。
- 我处理过长任务 WebSocket 推送、Redis/PostgreSQL 日志持久化和任务生命周期管理。
- 我的安全背景可以迁移到终端安全、系统诊断、开发者工具和可信 AI 场景。
- 我知道这个项目不是 HarmonyOS 项目，但我能清楚说明能力迁移路径。

## 20. 面试中尽量避免的说法

- 不要说“我做过 HarmonyOS 项目”，可以说“我做过 AI+安全+工程平台，能力可以迁移到 HarmonyOS 相关方向”。
- 不要说“TracePilot 完全自动化替代审计”，可以说“降低人工审计成本，并通过补丁验证提高结论可靠性”。
- 不要说“我们就是 RAG”，可以说“更准确是 Tool-Augmented Agent，后续可扩展漏洞知识库 RAG”。
- 不要只讲模型效果，要讲上下文管理、工具调用、验证闭环、日志可观测和失败恢复。
- 不要回避 Solidity 补丁中的工程坑，能讲 storage slot、版本差异和 initializer 反而更真实。