# TracePilot 工程化技术决策记录

本文档用于记录 TracePilot 前后端工程化过程中的关键技术选择、取舍原因和面试可讲点。它不是论文方法部分，而是面向“我如何把科研原型做成交互式、可复现平台”的工程说明。

## 1. 后端选择 FastAPI

**决策**：使用 FastAPI 封装任务创建、任务状态、宏观分析结果、日志分页、文件日志读取等 REST API，并提供 WebSocket 实时推送。

**为什么用它**：

- TracePilot 后端主体是 Python，FastAPI 可以直接复用现有 Agent、Tenderly 调用、Trace 处理和补丁验证逻辑。
- 原生支持 Pydantic 类型建模，适合把任务、日志、报告、宏观分析结果转为结构化 API。
- 异步能力较好，适合 WebSocket、外部 HTTP 工具调用和长任务状态推送。
- Swagger 文档自动生成，新队友可以直接在 `/docs` 调接口。

**为什么不用 Flask / Django**：

- Flask 更轻，但异步 WebSocket 和类型契约需要更多额外封装。
- Django 适合完整业务后台，但 TracePilot 的核心是长任务编排和 Agent 可观测性，Django 的管理后台、ORM 体系不是当前主要收益。

**面试可讲点**：我不是简单起一个接口，而是把科研 pipeline 拆成任务生命周期、实时事件流、持久化日志、宏观证据查询几类工程接口。

## 2. WebSocket + REST 的组合

**决策**：任务创建、查询、归档、删除等稳定操作走 REST；Agent 运行过程中的状态和日志走 WebSocket。

**为什么这么做**：

- REST 适合幂等查询和任务管理，例如 `/api/tasks`、`/api/tasks/{task_id}`。
- WebSocket 适合长任务实时观察，可以推送 Agent 中间输出、工具调用、错误、最终报告。
- 前端刷新后仍可通过 REST 拉任务快照和历史日志，再重新接入 WebSocket。

**为什么不用纯轮询**：

- Agent 输出频率不稳定，轮询要么延迟大，要么请求浪费多。
- 多 Agent 日志粒度较细，WebSocket 更符合“实时分析工作台”的体验。

**为什么不用 SSE**：

- SSE 只适合服务端单向推送。当前 WebSocket 支持心跳、断连检测和前端 PING/PONG，后续也可扩展客户端控制消息。

## 3. PostgreSQL + Redis + 文件日志三层日志存储

**决策**：

- PostgreSQL 保存任务记录、日志元数据、短日志和完整日志备份。
- Redis 缓存完整大段日志，提升点击日志详情时的读取速度。
- `agents/logs/` 保存各 Agent 原始文件日志，用于复现和离线审计。

**为什么要三层**：

- WebSocket 只解决实时可见，不解决刷新恢复和复现。
- Redis 快但不是长期审计存储。
- PostgreSQL 适合任务状态、分页查询、归档、删除等结构化管理。
- 文件日志保留最接近 Agent 原始输出的记录，便于科研复现实验。

**为什么 PostgreSQL 而不是 MySQL**：

- 当前日志、报告、结果中有大量半结构化文本，PostgreSQL 对 JSON/文本字段、复杂查询和后续分析扩展更友好。
- 未来如果要做证据包、报告索引、JSONB 字段查询，PostgreSQL 扩展空间更大。
- MySQL 也能完成基础 CRUD，但在半结构化审计数据和分析型查询上不是最优选择。

## 4. 宏观分析接口

**决策**：新增 `/api/tasks/{task_id}/macro-analysis`，从 `dataset/processed/{DApp}.json` 暴露宏观阶段结果，包括地址角色、攻击交易、辅助交易、调试目标、余额变化和宏观漏洞摘要。

**为什么要单独接口**：

- 原始最终报告是自然语言，不适合作为产品界面的唯一数据源。
- 宏观阶段已经有结构化结果，前端应直接消费这些结果，而不是再从 Markdown 中猜。
- 这能体现 TracePilot 的“宏观分析 -> 微观调试”双阶段架构。

**为什么不是全部塞进任务详情接口**：

- 任务详情是轻量状态快照，宏观分析结果可能很大。
- 分离接口可以按需加载，也便于后续加模式区分、分页或缓存。

## 5. 宏观分析面板 MacroAnalysisPanel

**决策**：前端新增宏观审计面板，把地址角色、攻击/辅助交易、调试目标做成可扫描的审计视图。

**为什么要做**：

- 只看日志时，用户不知道哪些地址和交易是核心证据。
- 面试或演示时，可以清楚讲出系统先做“角色识别和交易筛选”，再进入 Trace Debug。
- 它把论文里的中间产物变成了可解释 UI。

**为什么不用大段 Markdown**：

- Markdown 容易淹没关键信息，结构化卡片更适合快速判断。
- 地址、交易哈希、函数签名、余额变化天然适合表格/卡片/Tag。

## 6. 模式化 Tabs

**决策**：引入 `report / learn / auditor / raw` 四种视图模式，不同模式有不同默认 Tab 和功能顺序。

**为什么要做**：

- 普通用户只需要结论和关键证据，不应该被 Raw Logs 干扰。
- 学习用户需要案例背景和攻击阶段解释。
- 审计用户需要宏观角色、交易分类和证据链。
- 开发/调试用户才需要原始日志、文件日志和完整时间线。

**为什么不是只做一个大工作台**：

- 多 Agent 系统输出很多，单页堆叠会降低可读性。
- 模式化视图能把同一套底层数据包装给不同角色，这是产品化能力。

## 7. LearningGuidePanel

**决策**：新增学习导览面板，结合 DApp 背景、外部链接、宏观交易分类和攻击阶段解释。

**为什么要做**：

- TracePilot 不只是“给最终报告”，还可以帮助用户理解漏洞案例。
- 等待 Agent 运行时，用户可以先读漏洞背景、报告链接和关键交易入口。
- 对 SushiSwap 等案例，能把“背景 -> 辅助交易 -> 攻击交易 -> 状态证据 -> 调试目标”串成教学路径。

**为什么不直接翻译最终报告**：

- 审计语义精度很重要，机器翻译可能改变漏洞结论。
- 当前只翻译 UI 标签和解释框架，原始模型输出和证据保持原文，更安全。

## 8. Raw Mode 隔离

**决策**：将原始日志流、Agent 文件日志和完整时间线集中放到 Raw 模式；报告、学习、审计模式默认不展示 Raw Logs。

**为什么要做**：

- 普通用户看报告和关键证据即可，Raw Logs 会制造认知负担。
- 工程调试仍然需要完整日志，所以不能删除，只是放到专业入口。
- 这能回答面试中的“如何避免多 Agent 输出过载”的问题：通过角色化视图和信息分层。

## 9. 日志分页恢复接口

**决策**：新增 `/api/tasks/{task_id}/logs`，支持 `limit` 和 `before_id`，从 PostgreSQL 分页加载历史日志。

**为什么要做**：

- WebSocket 适合实时，不适合一次性恢复超长历史。
- 已完成任务或刷新页面后，前端需要按页恢复历史记录。
- 分页可以避免数据库和浏览器一次性加载几千条大日志。

**为什么用 `before_id` 而不是 offset**：

- 日志持续写入时，offset 容易因为新数据插入而漂移。
- 自增 ID 游标更稳定，适合“向上加载更早日志”的场景。

## 10. 前端日志虚拟滚动

**决策**：`LogStream` 使用固定行高估算的虚拟滚动，不引入额外虚拟列表依赖。

**为什么要做**：

- Agent 日志可能达到几千条，全部渲染 DOM 会卡顿。
- 虚拟滚动只渲染可视区域附近的日志，提升长任务体验。
- 当前实现足够满足 MVP，后续可替换为 `react-window` 或 `@tanstack/virtual`。

**为什么不马上引入第三方库**：

- 当前项目依赖已经较多，先用轻量自研方案验证交互。
- 日志卡片高度有波动，第三方库仍需要适配和测量成本。
- 后续如果日志量继续扩大，再引入成熟虚拟滚动库更合适。

## 11. 轻量中文模式

**决策**：新增 `i18n.ts`，支持中文/英文 UI 标签切换，但不翻译模型原始输出、证据和最终报告正文。

**为什么要做**：

- 中文界面更适合组会演示、国内面试和新手上手。
- 只翻译导航、标签、字段名，成本低且风险小。
- 审计报告、交易证据、模型输出保持原文，避免翻译导致含义偏差。

**后续可扩展**：

- 增加报告摘要的“可选翻译”按钮，而不是自动翻译全部内容。
- 对术语建立固定词表，例如 `attack transaction = 攻击交易`、`auxiliary transaction = 辅助交易`。

## 12. Markdown/JSON 导出

**决策**：最终报告支持 Markdown 审计报告和 JSON evidence package 导出。

**为什么要做**：

- Markdown 适合论文汇报、审计记录和人工阅读。
- JSON 适合复现实验、二次分析和后续平台集成。
- 这能支撑简历中的“可交互、可复现平台”表述。

## 13. Evidence Intelligence 证据智能筛选

**决策**：在前端增加证据评分与证据健康度面板，对报告结论关联的 evidence 进行排序、打分和风险提示。

**为什么要做**：

- 多 Agent 系统会产生大量中间输出，用户真正关心的是哪些证据能支撑结论。
- 只展示日志会造成信息过载，证据评分能把“看日志”升级为“看证据链”。
- 面试中可以用它回答“如何避免多 Agent 幻觉、结论漂移”：不是盲信最终报告，而是要求结论能被交易哈希、工具调用、Agent 结果和宏观分析结构化证据支撑。

**当前评分依据**：

- 证据来源：交易哈希、工具调用、结构化系统输出、Agent 日志、报告摘要。
- 证据置信度：高/中/低置信链接。
- 消息类型：工具调用、Agent result、错误/告警。
- 内容信号：交易哈希、合约地址、漏洞关键词、Trace、Storage、Patch、Replay 等。
- 噪声惩罚：纯运行统计、Token 统计、空泛 task output、过短上下文。

**为什么先不用新的 LLM Agent 判断证据质量**：

- 证据筛选本身应尽量可解释、可复现，启发式评分可以明确说明每个分数来自哪里。
- 新增 LLM Judge 会增加成本、延迟和二次幻觉风险。
- 当前阶段先用 deterministic scoring 做产品 MVP，后续可在强证据不足时再触发 LLM 复核。

**为什么放在前端而不是后端**：

- 证据评分目前主要服务 UI 排序和展示，不改变后端审计事实。
- 前端实现迭代快，不影响后端任务执行稳定性。
- 后续如果评分逻辑稳定，可以沉淀为后端 evidence API，支持导出和权限控制。

**后续可扩展**：

- 加入跨 Agent 一致性检查，例如 Transaction Judge 与 Trace Debugger 是否指向同一交易。
- 加入“证据缺口”提示，例如缺少交易哈希、缺少工具验证、缺少补丁回放结果。
- 在导出的 JSON evidence package 中保存 evidence score，形成可复查的审计证据包。

## 14. Cross-Agent Consistency 多 Agent 一致性检查

**决策**：在前端新增多 Agent 一致性检查，对宏观交易筛选、Trace Debug、根因定位、补丁生成和补丁验证之间的连续性进行打分。

**论文依据**：

- 论文将 TracePilot 描述为两阶段框架：先从交易序列中提炼全局故障理解，再进行聚焦的 Trace 探索来隔离故障逻辑。
- 论文强调多 Agent 系统存在 hallucination amplification 风险，即错误信息可能在 Agent 网络中传播。
- 论文的核心卖点之一是 self-verifiable，即不能只依赖模型报告，而要通过补丁验证等机制验证定位结果。

**为什么要做**：

- Evidence Intelligence 能判断“单条证据强不强”，但还不能判断“多个 Agent 的结论是否在同一条链上”。
- 多 Agent 一致性检查用于发现结论漂移：例如宏观阶段选出的攻击交易没有被 Trace Debugger 继续分析，或者 Debugger 找到的根因函数没有进入补丁阶段。
- 这能支撑面试回答“如何避免多 Agent 互相幻觉、结论漂移”：通过跨阶段关键实体对齐，而不是只看最终报告。

**当前检查项**：

- **Macro transaction selection -> Trace debugging**：宏观阶段选出的 `transactions_need_analyze` / attack transaction 是否被 Transaction Debugger 或最终报告引用。
- **Attack transaction agreement**：是否有多个 Agent 引用同一攻击交易，避免各说各话。
- **Root cause function -> Patch continuity**：根因定位/Trace Debug 中提到的函数是否被 Code Patcher、Transaction Judge 或最终报告继续引用。
- **Patch verification loop**：是否同时存在 patch/fix 信号和 verification/replay/success/failure 信号。
- **Root cause quorum**：root cause 相关语言是否出现在多个关键 Agent 中，而不是只出现在最终报告。

**为什么先在前端实现启发式检查**：

- 当前目标是产品可观测性和演示，前端可以快速读取已有日志、最终报告和宏观分析结果。
- 启发式规则可解释、可复现，能直接显示每项检查的证据和建议。
- 不新增 LLM 调用，避免引入额外成本和二次幻觉。

**局限性**：

- 函数名抽取目前基于正则，可能会混入普通函数或遗漏复杂签名。
- 不同 Agent 输出格式不完全统一，检查结果依赖日志中是否显式提到交易哈希和函数名。
- 后续应推动后端和 Agent 输出结构化字段，例如 `root_cause_functions`、`debug_target_txs`、`patch_target_functions`、`verification_result`。

**后续可扩展**：

- 将一致性检查沉淀为后端 `/consistency-analysis` 接口，保存到证据包。
- 引入结构化 Agent 输出 schema，减少从自然语言日志中抽取实体。
- 当一致性分数过低时，触发一个轻量 Review Agent 生成复核问题，而不是直接相信最终报告。

## 15. Docker Compose

**决策**：使用 Docker Compose 同时启动 backend、PostgreSQL、Redis。

**为什么要做**：

- 新队友不需要本地手动安装数据库和缓存。
- 组会演示时环境更稳定。
- 更容易复现“后端 + DB + Redis”的完整链路。

## 面试总结话术

我做的不是简单把 Agent 输出展示到页面，而是围绕长任务、多 Agent、长日志和审计可复现性做了工程化封装：

1. 后端用 FastAPI 把科研 pipeline 封装成任务 API、WebSocket 实时推送、宏观分析查询和日志分页恢复。
2. 存储层用 PostgreSQL、Redis、文件日志组合，分别解决结构化查询、快速详情读取和原始证据保留。
3. 前端按用户角色拆成报告、学习、审计、原始调试四种模式，避免所有人都被多 Agent 日志淹没。
4. 对长日志做分页恢复和虚拟滚动，让系统能支撑更长的真实任务。
5. 对宏观分析结果做结构化展示，把论文中的中间推理结果转化为可解释、可演示、可复现的产品能力。

## 16. Backend Automated Review Service 后端自动化审查接口

**决策**：新增 `/api/tasks/{task_id}/automated-review`，在后端基于任务状态、持久化日志、宏观分析结果和最终报告做确定性的跨 Agent 一致性审查。前端优先消费后端审查结果；如果接口不可用，再回退到本地启发式检查。

**为什么先不新增 LLM ReviewAgent**：

- 自动审查首先要解决“结论是否可复现、证据链是否连续”的工程问题，确定性规则比新的 LLM Judge 更容易解释和复查。
- 新增 ReviewAgent 会增加 Token 成本、延迟和二次幻觉风险。论文也强调多 Agent 系统存在 hallucination amplification，审查层不应该一开始就完全依赖另一个模型结论。
- 当前系统已有宏观分析、Trace Debug、Patch、Verification 等中间产物，足够先做实体对齐：交易哈希、函数名、补丁信号、验证信号、根因信号。
- 后端接口可以被前端、导出证据包、未来权限系统共同复用，比只放在前端更像平台能力。

**当前检查项**：

- `macro-to-debug`：宏观阶段选出的调试交易是否进入 Trace Debug 或最终报告。
- `attack-classification-overlap`：多个 Agent 是否引用同一攻击交易，避免各说各话。
- `root-to-patch`：根因/调试阶段提到的函数是否进入补丁或验证阶段。
- `verification-loop`：是否同时存在补丁信号和验证/回放/成功失败信号。
- `root-cause-quorum`：根因语言是否出现在多个关键 Agent，而不是只出现在最终报告。

**为什么这算后端业务能力而不是前端美化**：

- 它读取并融合后端任务、数据库日志、宏观分析 JSON、最终报告，是对 TracePilot 多阶段 pipeline 的二次审查。
- 它输出统一 JSON schema：`score`、`status`、`checks`、`agent_signals`、`shared_transactions`、`shared_functions`、`next_actions`。
- 后续可以直接写入 evidence package 或审计报告导出链路，成为“可复现平台”的一部分。

**后续扩展**：

- 推动 Agent 输出结构化字段，例如 `debug_target_txs`、`root_cause_functions`、`patch_target_functions`、`verification_result`，减少从自然语言日志中抽取实体。
- 当 deterministic review 发现高风险或证据缺口时，再触发轻量 ReviewAgent 生成复核问题，而不是每个任务都默认调用 ReviewAgent。
- 将审查结果持久化到数据库，支持历史对比、失败任务复盘和报告导出。
