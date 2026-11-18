# 测试策略

按项目模块查看测试类型、代表用例、问题和报告入口，请参阅[模块测试总览](testing-catalog.md)；各模块的聚合指标见[测试与质量指标台账](testing-metrics.md)。

## 测试层次

| 类型 | 验证内容 | 示例 |
| --- | --- | --- |
| 单元测试 | 纯领域逻辑、预算计算、路由规则、参数约束 | 未授权参数被拒绝、预算扣减正确 |
| 契约测试 | DTO / Protocol 与适配器的一致性 | RetrievalToolRequest 校验、ToolDescriptor 兼容 |
| 集成测试 | 模块边界及真实或模拟外部服务交互 | Router 到 Tool Executor、检索客户端到服务 |
| 安全测试 | 用户数据访问边界、Skill 包安全、注入防护、权限拒绝、沙箱 capability | 路径逃逸和非法 Skill 被拒绝、高影响操作无确认不执行、默认无网络 |
| 端到端测试 | 代表性请求从入口到最终响应的完整行为 | clarify、检索工具调用、受控执行路径 |
| 评测 | 质量、证据相关性、策略一致性和成本 | 检索命中、回答引用、预算符合性 |

## 旧 Phase 测试记录索引

- **Phase 0 持久化**：SQLite 检查点跨连接恢复、多 conversation_id 隔离、会话索引重启恢复、Markdown 路径边界和原子写入、本机 API 的 CORS 与会话删除联动。
- **Chat API 首版**：ContextBuilder 会话历史与记忆装载、AgentRuntime 注入、消息提交/历史查询、无运行时的 503、Origin 拒绝及 LangGraph thread 状态读写。
- **状态 WebSocket**：合法 Origin 握手、初始状态快照、ping/pong 关联、非法/超大帧拒绝及不可信 Origin 关闭。
- **Phase 1**：入口请求校验、Router 分支、工具授权、检索工具契约、EvidenceSet 校验、Skill 本地发现/修改/校验、用户确认路径、外部服务错误、超时和 L4 拒绝路径。
- **Phase 2**：DAG 依赖注入、子 Agent 隔离上下文、局部重规划次数上限，以及 HostPilot 对外部检索工具的调用行为。
- **Phase 3**：各平台沙箱后端的 capability 拒绝、路径边界、资源限额、进程清理、超时及审计；覆盖后端语义差异。
- **外部 RAG 集成**：在 HostPilot 侧验证凭证保护、用户范围映射和请求/响应契约。RAG 服务内部的多用户隔离、索引隔离和 Agentic Retrieval 循环由独立项目测试。

## 新架构专项测试

- **Skill 契约与注册表**：`skill.yaml` schema、必填字段（含 `side_effect_level`）、重复 skill_id、依赖预检、禁用 Skill、路由签名字段；JSON Schema 仅允许本地 `$ref`，并验证深度/节点上限；覆盖 Markdown-only、YAML-only、双格式默认 YAML 和显式 `yaml` / `deepagents` / `both` 配置，并验证一次请求只分发到唯一执行入口。
- **Skill Runtime**：按 skill_id 区分内置 Executor 插件与 YAML steps；验证输入/输出 schema、线性兼容顺序、DAG 依赖、并行分支与汇聚、依赖失败跳过、子 Skill 委派、上下文快照和失败回滚。
- **Plan Orchestrator 当前实现**：验证 DAG 拓扑 wave、独立 task 并行、并发上限、下游依赖等待、wave 失败后收集兄弟结果、取消清理、已注册可逆副作用的逆序补偿、in-flight 任务结果不确定快照、缺失依赖/环拒绝、有限重规划和失败副作用保留。另验证 Planner 只获取 Skill 能力摘要和工具检索能力，Task Executor 在执行阶段选择授权 Skill 或检索有界工具候选，Plan Validator 拒绝未知或未授权 ID，MCP action 透传预算和审批 Provider。既有文件覆盖有受治理补偿；远程 MCP action 没有可信逆操作契约，完整步骤级状态快照不持久化。
- **业务 Skill 评估与 Plan 兜底**：验证模型化准入和结果语义评估、confidence 阈值转 Plan、普通 Chat JSON 输出与本地 schema 校验、评估 Token 预算和用量上报。首轮真实模型 selector-only 评测使用合成 Skill，严格路由匹配 26/30；Runtime 定向报告定位到评测输入映射缺失和 Plan `tool_id` 格式缺失，修正后待少量模型用例复测。ToolExecutor 的工作区路径拒绝、审批缺失拒绝、批准后仅进入内存 stub 已用 0 模型 Token 单独验证。详见[评测计划](stage-c-model-evaluation.md)、[selector 结果](test-results/2026-11-04-stage-c-live-model-selector-evaluation.md)、[Runtime 结果](test-results/2026-11-04-stage-c-live-runtime-stub-evaluation.md)、[诊断复核](test-results/2026-11-05-stage-c-runtime-diagnostic-review.md)和[工具边界补充验证](test-results/2026-11-04-stage-c-runtime-boundary-supplement.md)。另验证副作用等级策略、快照完整性、幂等去重、补偿门、禁止无新信息重跑和全局预算熔断。
- **循环与委派安全**：最大步骤/轮次熔断、委派深度和递归环拒绝、父 Skill 子 Skill/tool allowlist、未声明目标默认拒绝、Planner 提示/Plan Validator 策略一致、相同失败 Plan task 不重试、有限重规划、MCP 调用不自动重试、预算传播、取消与超时清理。
- **共享模型预算**：覆盖 Agent、Skill Selector、YAML `llm.complete`、Plan/Verifier 的预算边界；并行请求共用 RequestBudget 的模型调用锁，输出上限按余量收紧，真实用量立即记账。用量超额后预算状态锁存，排队请求不得继续；确认响应和 ChatService 不重复扣费。
  一个 OpenAI 兼容服务的真实低成本探针已验证 12,000 预算成功完成 DeepAgents 请求、6,000 预算返回 429 并在 Skill 执行阶段熔断，详见[报告](test-results/2026-11-07-live-agent-budget-smoke.md)。后续 Plan、单步 YAML 和三根并行 YAML 的真实 Provider 结果见[补充报告](test-results/2026-11-08-live-plan-yaml-budget-smoke.md)；同一聊天请求中混合 Plan 与 YAML 仍待验证。
- **工作区与会话管理**：验证原生选择器返回目录后的读取/写入权限探测、只读写入在审批前拒绝、无读取权限时不切换、SQLite 重启恢复、会话确认删除及检查点/摘要/待审批提案清理。图形文件对话框通过注入选择结果测试，真实桌面弹窗需本机手动验收。
- **MCP Tool Facade**：内置进程内 MCP 会话、每个远程 Server 的 Streamable HTTP 会话、认证和连接失败、跨 Server 同名工具隔离；远程输入 Schema 的结构边界、局部引用、非法参数拒绝必须在策略/审批/调用前验证；本地/远程工具加载和连接清理均有时限；启动取消须关闭当前及此前已启动会话；调用超时须取消本地等待并留下审计事件。远程 Server 是否停止执行其副作用不能由客户端保证。
- **记忆控制器**：入口预召回、子任务按需召回、归档筛选、会话/用户范围隔离；Executor 与 YAML steps 不得直接访问持久化实现。
- **模型 Provider**：统一请求/响应契约、不同 LLM 接入类型的配置、超时、健康状态及降级策略；故障切换不能重复执行有副作用的工具调用。
- **客户端配置与观测**：模型/MCP 凭证不回显或持久化；STDIO args 的凭证型 option、assignment 和 Authorization header 在后端及 JSON 导入入口拒绝；MCP 启停状态、Trace 脱敏和异常可定位。
- **活跃路由评测隔离**：route-only API 可以调用当前检索器和路由模型并返回分类结果，但不得进入 Skill Runtime、Plan、工具执行或回答生成；评测上下文不得注入共享长期记忆。评测器记录未覆盖的启用 Skill、HTTP 中断及部分结果，覆盖不全或请求中断时不得登记为完整质量基线。
- **Stage E 页面验收**：组件测试覆盖表单和错误态；跨页面验收使用主导航操作，验证 Harness 配置保存/重载、MCP 参数与 JSON 模式互斥、Skill、Trace 与 Audit 页面加载。模拟 API 的 Vitest 流程属于 UI 集成测试；Playwright 真实浏览器 E2E 使用独立临时数据目录启动 FastAPI、Vite 和本地 FastMCP 假服务，验证 HTTP、WebSocket、CORS、Harness/MCP 配置持久化、远程工具发现、Skill 启停/删除、注入 Workspace 选择器、沙箱权限升级、Trace 详情脱敏及聊天审批 WebSocket 决策。审批专用 Runtime 不执行真实 ToolExecutor；真实桌面目录对话框仍需本机验收。
- **Trace 持久化与查询**：Trace 只保存白名单元数据；验证请求关联、时间顺序、保留上限、API 未找到行为，以及对话/工具正文和凭证不进入查询结果。
- **Audit 持久化与查询**：验证 SQLite 留存期限与事件数上限、trace/时间筛选、offset 分页、只读 API、CSV 导出公式转义及响应不包含原始参数和凭证。

新架构首轮 Stage B 注册表和路由测试结果记录在 [验证记录](test-results/2026-11-02-stage-b-yaml-skill-registry.md)。
业务 Skill 副作用等级与准入/结果评估见 [Stage C 语义评估验证](test-results/2026-11-03-stage-c-semantic-assessment.md)；取消补偿实现见[专项报告](test-results/2026-11-03-stage-c-compensation-implementation.md)。
Stage C Runtime 与 Plan Orchestrator 的定向测试和质量检查记录在 [验证记录](test-results/2026-11-03-stage-c-runtime-orchestration.md)。
Stage C 默认 Chat API 装配、MCP action 和模型 Plan 适配器的验证记录见[集成报告](test-results/2026-11-03-stage-c-integration.md)。
模型化准入与结果语义评估的验证记录见[专项报告](test-results/2026-11-03-stage-c-semantic-assessment.md)。
Plan DAG 并行调度及并发审批保护的验证记录见[专项报告](test-results/2026-11-03-stage-c-plan-parallelism.md)。
取消补偿协议及既有文件恢复的验证记录见[专项报告](test-results/2026-11-03-stage-c-compensation-implementation.md)。
Plan Planner、Verifier 和 Skill 评估器使用普通 Chat JSON 输出的兼容性记录见[专项报告](test-results/2026-11-04-stage-c-json-model-output.md)。
首轮真实模型 Skill 路由评测记录见[专项报告](test-results/2026-11-04-stage-c-live-model-selector-evaluation.md)。
Stage F YAML Skill 工具 allowlist 的注册表、Runtime、Planner 与 Plan Validator 验证见[专项报告](test-results/2026-11-07-skill-tool-authorization.md)。
远程 MCP inputSchema 边界与调用参数校验见[专项报告](test-results/2026-11-07-remote-mcp-schema-validation.md)。
STDIO args 凭证拒绝和无回显验证见[专项报告](test-results/2026-11-07-stdio-credential-argument-isolation.md)。
MCP 工具发现超时、调用取消及有界连接清理见[专项报告](test-results/2026-11-07-mcp-timeout-cancellation.md)。
Plan 有界重规划、同任务失败去重、工具调用无自动重试及 Audit trace 关联见[专项报告](test-results/2026-11-07-retry-audit-boundaries.md)。

## 覆盖与结果

覆盖率按模块查看，不以单一总百分比替代关键路径测试。策略、用户数据范围、预算和沙箱代码应覆盖允许与拒绝分支；Skill 测试还要覆盖不可信指令处理、包路径边界、确认取消和审计。覆盖率目标待首个完整测试基线建立后结合风险设定。本地 Skill 首版的命令、结果和已知缺口记录在 [验证记录](test-results/2026-10-29-skill-v1.md)。

每次测试记录：提交/版本、执行命令、环境、通过/失败/跳过数量、覆盖率（如有）、失败原因、已知缺口和后续优化项。测试失败应先分类为代码缺陷、环境/依赖问题或测试本身问题，再决定修复。按模块持续维护测试状态和优化前后质量指标，见[测试与质量指标台账](testing-metrics.md)。通过数、安全边界验证和准确率/命中率等质量指标应分开记录；前后对照应使用相同数据集版本、预算和环境，缺少测量时明确标为未测。

## 测试数据

使用合成或脱敏数据。测试用户和会话应能验证未经授权的数据访问被拒绝；外部模型调用默认使用 fake/stub，只有明确的集成测试配置才访问真实服务。不得将生产凭证或个人数据带入测试夹具。
