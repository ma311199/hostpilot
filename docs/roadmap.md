# 实施路线与新架构迁移

新架构目标以[新架构文档](新架构文档.md)、[ADR-0012](decisions/0012-business-skill-fast-path-and-plan-fallback.md) 和后续修订的 [ADR-0029](decisions/0029-three-skill-pipelines.md) 为准：保留 `direct_answer`、`clarify`、`orchestrator_plan` 三个内置 Skill；普通单轮问答走 `direct_answer`，复杂或需要工具/多步执行的请求进入 Plan；不建立独立顶层 ReAct 工具路由，Plan 工具任务内部使用受限 ReAct 子 Agent。MCP 保留内置进程内 FastMCP，并支持本地 STDIO 子进程和远程 Streamable HTTP Client；前端为本地浏览器 Web。

本文区分**当前已交付实现**与**目标迁移路线**。新架构尚未全部实现，不应把目标组件标成当前能力。以下迁移阶段按依赖顺序排列；两种 Skill 执行格式的选择规则见 [ADR-0013](decisions/0013-dual-skill-formats-and-execution-selection.md)，具体框架保留范围由实现适配器时评估。

## 当前已交付基线

- Vue 3 本地浏览器 Web 提供项目看板、聊天、模型与 Harness 配置、工具目录、MCP 连接管理、Skill 包管理、Trace 和 Audit；阶段数据随前端发布。
- FastAPI 提供会话、历史、聊天 WebSocket、健康状态及配置接口；SQLite 保存会话检查点，Markdown 保存会话摘要与长期记忆。
- 模型运行时当前配置 OpenAI 兼容 Chat API；路由、Plan、评估、`llm.complete` 和 DeepAgents/LangGraph Agent 图均通过统一 Provider 契约调用。Agent 图仍通过 LangChain 接口使用 Provider 桥接，其他 LLM 接入类型尚未实现。
- Router 仍负责显式命令/工具路由；配置模型后的默认 Chat Agent Runtime 已接入顶层 Skill 选择器，可分流到 `direct_answer`、`clarify`、`orchestrator_plan` 或启用的业务 Skill，并保留原 DeepAgents Runtime 作为未选择时的兼容路径。
- **Skill 格式分流与 Plan 兜底已进入实现：**唯一高置信候选按格式进入 YAML SkillEngine 或 Markdown 执行器；路由评分不一致、多个中置信候选以及执行/评估失败会转 Plan 并携带原因。Plan 先按候选 Skill 能力分解目标，未覆盖的目标再按任务默认最多检索五个授权工具。定向测试和真实链路验证尚待完成。
- **Skill 混合路由已实现并建立首轮 route-only 基线：**规则优先的上下文语义消解、字段化 BM25（name 1.5、description 1、其他元数据项 1.5；缺失字段用字段均分填补）、description 向量召回、1:1 RRF、本地 ChromaDB，以及可选 Reranker。候选最终由轻量 LLM 给出选择与置信度，轻量模型未配置或失败时使用主 LLM；业务高置信门槛为 top-1 `>=0.85` 且 margin `>=0.30`。2026-11-14 活跃 route-only 评测为 18/20 路由正确，业务 Skill 精确命中 11/11，RRF Recall@3 为 11/11、MRR 为 0.8485；仍需扩展启用 Skill 和边界用例，并完成默认 Chat Agent Runtime 端到端质量评估。报告见[route-only 实测](test-results/2026-11-14-live-routing-route-only.md)，完整评测计划见[Stage C 评测方案](stage-c-model-evaluation.md)。真实 ChromaDB 持久化和重启恢复验证见[历史验证报告](test-results/2026-11-11-hybrid-skill-routing-validation.md)。
- DeepAgents `SkillRegistry` 加载 `SKILL.md` Markdown 包；YAML 注册表与元数据 Router 已交付，Stage C Runtime 已装配到默认 Chat API。两种格式长期并行支持，不做格式替代；统一 Skill Catalog 合并用户与只读 bundled 包，按包格式提供执行入口，双格式包可显式选择 YAML、Markdown 或 `both`，默认 YAML。偏好在 SQLite 持久化并即时作用于分类器、YAML Runtime 和 Markdown 输入快照；`both` 请求只会进入一个入口。历史 `deepagents` 偏好会在启动时迁移为 `markdown`，旧 API 请求值也会归一化为新值。YAML action 与 Plan 工具任务经过 MCP Provider 的 Tool Executor 治理链，Plan 适配器具备模型 Planner/Verifier、工具与子 Skill 执行器。已附带只审查用户所贴代码的 `business/code-review-check`，使用受预算约束的只读 `llm.complete` action；业务语义评估、Plan DAG 并行和既有文件的受治理取消补偿已实现。首轮业务路由 route-only 基线和澄清后多轮集成验证已完成；更广泛的业务路由与完整默认 Chat API 端到端质量、可逆远程 MCP action 的可靠补偿契约，以及可从 Trace 诊断之外恢复 Plan 的持久状态快照仍未完成。
- 内置 MCP Server 和 Client 在 HostPilot 进程内通过内存会话连接。外部 Server 可通过 `HOSTPILOT_MCP_SERVERS` 或管理页面配置为本地 STDIO 子进程或独立 Streamable HTTP Client；外部工具以 Server 名称前缀区分重名。
- 所有当前 MCP 工具调用已接入 Tool Registry、Policy Engine、Tool Executor、预算、审批和结果验证。Stage D 已增加统一 Tool Facade：聊天路由、YAML Skill、Plan 与工具目录通过 facade 访问稳定工具描述和调用；具体连接由 MCP Provider 管理。Stage E 已提供本地 STDIO 与远程 Streamable HTTP Server 配置 CRUD、启停、密钥状态与运行时 Provider 重建；非敏感配置本地持久化，按 ADR-0014，MCP `api_key` 只保存在进程内存，重启后重新输入。Embedding/Reranker Key 的本地持久化按 ADR-0015 管理。
- RAG 当前由内置 `retrieve_knowledge` MCP 工具通过普通 HTTP 调用独立 RAG 服务；它不是 MCP Server。
- 当前有确认门、Trace/Cost/Audit 契约及部分输出校验。Stage D 已增加 Memory Controller：ContextBuilder 统一经 Controller 读取当前会话摘要与共享 Markdown 记忆，归档候选由本地规则评分；低分丢弃，中/高分仍经人工审批后分层落盘。Plan 子任务也会按任务目标通过 Controller 召回范围内的记忆。召回按段落执行本地 BM25 风格词项排序，并在未命中片段后按会话优先顺序回退；语义检索仍待评估。Skill、Plan 任务、工具与审批 Trace Span 已按请求 ID 关联。成功 Plan 子任务结果和版本化指纹经 TaskStore 持久化；新 Turn 对同 Session 的精确命中逐项展示历史输出，并等待用户选择复用并跳过或忽略并执行。无审批能力或超时会澄清并停止、不复用也不执行命中任务，断连取消等待中的 Turn；Trace 记录来源与选择但不记录结果正文。Harness 可配置 Planner 参考的历史成功结果窗口（默认 32 条、65,536 字符；范围 1–128 条、1–262,144 字符）；窗口仅用于 Planner 参考，不改变当前 Turn 完成状态或 Task Executor 输入，SQLite 历史记录仍保留。Planner 另获得有界安全失败摘要；失败结果不触发自动重试。PlanRun 现已具备版本化 Blackboard/DAG 快照、Host 中断标记、用户确认后的跨 Host 恢复、任务级 checkpoint 和工具幂等调用日志；旧 TaskRun 无快照时仍不可恢复。ChatPanel 通过共享 `/ws/chat` 的 `plan.resume` 事件启动恢复，并沿用逐工具确认 Provider；HTTP 恢复没有交互审批能力时对高影响调用 fail closed。恢复边界、人工核实和存储留存策略见 [Runtime Plan 持久恢复验证](test-results/2026-11-22-runtime-plan-resume-idempotency.md)。
- **Runtime Core 正在分阶段拆分：**`hostpilot.runtime` 管理每个 FastAPI lifespan 的 RuntimeHost、共享 RuntimeContext、请求级 RequestContext、依赖排序、服务注册及逆序关闭。ChatService 为每个 Turn 创建一个 RequestContext，并将同一对象贯穿 ChatAgentRuntime、Skill Selector、SkillEngine、Plan fallback 和 DeepAgents 结果评估；Chat Runtime 插件 API 已升至 2.0。LangGraph 在工具上下文边界映射为兼容的 `snapshot`、`budget`、`confirmation_provider` 字段，模型和工具 Port 暂保留既有调用形状。默认 Chat Runtime 已切换为 `builtin.runtime-chat`：直接对话使用 Runtime-native Model/Tool 循环、Prompt/Memory Builder 和 LangGraph Checkpoint State Adapter；YAML Skill 由 SkillEngine 执行，Markdown Skill 仍经统一 SkillExecutor 契约委托 DeepAgents/LangGraph。`builtin.deepagents` 保留为可显式配置的兼容 Chat Runtime，不再是默认控制路径。`builtin.state-services` 持有可替换 ConversationStore、SessionLifecycleStore、TurnStore、TaskStore 和 Checkpointer；Session 状态、跨 worker 操作租约及 Turn/Plan TaskRun 元数据经 SQLite 持久化，Host 租约默认 5 秒心跳/30 秒过期，非活动 Host 的残留运行记录会标记 `interrupted`，但不恢复执行。Runtime-owned TaskLifecycleManager 记录每次实际 Plan 子任务调用。`AgentLifecycleManager` 已管理 Chat Agent 代际、Turn 租约、配置替换和 Host 停机清理：新 Turn 使用最新 Agent，在途 Turn 继续使用原代直到释放租约。工具结果未知时的跨请求持久阻断与人工核实结果交接已实现；可能产生副作用的调用在派发前持久登记，只读工具可安全重试，人工结果按原 tool_call_id 写入 Checkpoint 后清除阻断。详见[Runtime 工具结果核实与恢复验证](test-results/2026-11-21-runtime-tool-outcome-resolution.md)。Runtime 已管理 Session 创建、访问租约、关闭排空、可重试清理和 Host 关停排空；deleted tombstone 防止旧会话回填复活；Plan 执行游标由版本化 PlanRun 快照恢复；Runtime-native Chat Turn 可由用户明确确认后从关联的持久 Checkpoint 恢复，并沿用原 Turn ID 和工具幂等键；Host 启动和查询不会自动派发执行。恢复使用新的 RequestContext、预算及当前授权与 Policy。Agent 框架私有状态和显式 builtin.deepagents 路径仍不支持恢复；Chat Runtime 装配现由 `builtin.chat-runtime-assembly` 通过 `hostpilot.chat-runtime-assembly` 服务创建候选实例，API 保留配置持久化、候选 Provider 构建和协调切换；具体组合实现已迁入 `orchestration.chat_runtime_composition`，兼容工厂 `create_configured_chat_agent_runtime()` 保留原签名并委托给该实现。其余插件边界和迁移范围见[ADR-0034](decisions/0034-runtime-core-and-plugin-lifecycle.md)、[ADR-0035](decisions/0035-runtime-chat-default.md)、[ADR-0036](decisions/0036-plan-task-cache-approval.md)及[Runtime 执行循环边界验证](test-results/2026-11-19-runtime-execution-loop-boundaries.md)。

**框架复用原则：**Runtime Chat 是 Agent 执行控制面。DeepAgents、LangGraph 等可继续提供 Skill 执行器、DAG 调度器、Checkpoint 或其他成熟能力，但通过插件/适配器接受 Runtime 的能力、预算、审批、取消、状态与恢复约束。迁移目标是替换 `builtin.deepagents` 的默认控制路径，不以移除所有框架依赖为目标；只有某项框架能力不再被插件使用时，才移除对应依赖。

**Runtime Chat 迁移实测（2026-11-20 至 2026-11-21）：**在 `builtin.runtime-chat` 下，gpt-6-luna 四例全通过：直接回答、只读工具、代码审查 Skill 缺参澄清，以及工具结果已 checkpoint 后注入模型失败并在同一 session 重试。总计 19,636 Token；恢复场景 handler 仅执行 1 次，真实工具副作用为 0。glm-5.3 通过有材料代码审查例（6,339 Token、36.604 秒、0 工具）、两步只读 Plan 例（13,198 Token、81.937 秒、2 次内存工具调用）和跨进程恢复例（946 Token、10.306 秒、1 次只读工具调用）。跨进程恢复使用不同 Python worker PID、相同 session/SQLite checkpoint，resume worker 未重复执行工具；已 checkpoint 工具结果的跨进程恢复通过真实模型验证。Plan 例的多步 Skill 能力评估未通过后转交既有 Plan 路径，最终路由、Skill ID、两次读取和回复断言均符合预期；它验证 Runtime Chat 到 Plan 的集成，不代表 Plan 已迁移为 Runtime-native。代码审查回复识别 SQL 注入并建议参数化，Critical 严重级别仍需结合上下文复核。工具结果未知窗口现由故障注入单测及 SQLite + Chat API 集成测试共同覆盖：handler 已执行而结果 checkpoint 失败后，Runtime 不再调用模型或工具，跨应用重启重试会返回 `clarify` 并要求用户核实；内存工具计数保持 1，未执行真实外部副作用。默认 Chat Runtime 已由 `builtin.deepagents` 切换至 `builtin.runtime-chat`；DeepAgents/LangGraph 仍提供 Markdown Skill 执行和 Checkpoint 实现。详细数据见[Runtime Chat 迁移评测](test-results/2026-11-20-runtime-chat-live-evaluation.md)、[未知工具结果恢复保护](test-results/2026-11-21-unknown-tool-outcome-recovery.md)、[SQLite + Chat API 恢复集成测试](test-results/2026-11-21-runtime-chat-sqlite-api-recovery.md)和[默认 Runtime 切换记录](test-results/2026-11-21-runtime-chat-default.md)。

**Skill 执行统一原则：**Runtime Chat 使用统一 Skill 执行契约承接两种长期支持的格式；Markdown 与 YAML 各自保留发现、解析和校验适配器，规范化后由 Runtime 统一调度和治理。格式选择与执行器后端独立；Runtime 按 `yaml` 或 `markdown` 选择注入的执行器，不要求由单一框架原生解释两种格式。

2026-11-20 已新增 `SkillExecutor.execute(SkillExecutionRequest)`：YAML SkillEngine 与 Markdown DeepAgents 适配器通过同一 Skill 调用点执行，保留格式专属解析与框架适配。格式偏好现统一使用 `yaml` / `markdown` / `both`；SQLite 启动迁移将旧 `deepagents` 值规范化，API 继续兼容旧客户端输入，前端、分类器和 Runtime 均按格式名称分流。

## 迁移阶段

### 阶段 A：稳定 Harness 契约与配置边界（已完成）

- 已在 [ADR-0010](decisions/0010-harness-contracts-and-configuration-boundary.md) 明确 Skill ID、MCP 工具稳定标识、通用 LLM Provider、Memory Controller 和 Runtime 输入输出边界。
- 将现有预算、策略、审批、错误与审计能力保留为所有 Skill 共用的横切服务。
- 明确本地浏览器 Web 配置边界：模型/MCP 配置跨重启持久化，API 不回传密钥；主 LLM、轻量 LLM 和 MCP Key 只在内存使用，Embedding 与 Reranker Key 存入本地 SQLite（ADR-0014、ADR-0015）。
- MCP 拓扑包含内置 FastMCP 进程内连接、本地 STDIO Server 子进程和独立远程 Streamable HTTP Client；暂不采用 `local::` / `team::` 命名空间。安全边界见 ADR-0016。

### 阶段 B：YAML Skill 契约、注册表与路由（已完成）

- 已实现统一目录、格式兼容的执行方式管理与 SQLite 偏好持久化；根据 [ADR-0013](decisions/0013-dual-skill-formats-and-execution-selection.md) 两种执行形式长期并行，不自动转换格式。分类器基于启用状态与偏好生成入口候选，DeepAgents 只接收选中 Skill，`both` 每次请求单入口执行。
- `YamlSkillRegistry` 实现 schema v1 校验、Draft 2020-12 JSON Schema 校验、唯一 Skill ID、依赖预检、资源大小/类型、路径和符号链接边界。
- schema v1 要求声明 `side_effect_level`（`read_only` / `idempotent` / `reversible` / `irreversible`），Stage C 使用它约束执行后升级策略。
- `MetadataSkillRouter` 仅基于 YAML examples、tags、intent_summary 返回可信度足够的 `skill_id`；弱匹配返回空结果，不执行 Skill 或授予工具权限。
- YAML 路由以结构化元数据为准；DeepAgents Markdown Skill 保留独立执行入口。双格式包默认选择 YAML。
- 首轮注册表测试见 [Stage B 验证记录](test-results/2026-11-02-stage-b-yaml-skill-registry.md)；当前业务 Skill 路由与副作用等级原则见 [ADR-0012](decisions/0012-business-skill-fast-path-and-plan-fallback.md) 和 [ADR-0029](decisions/0029-three-skill-pipelines.md)。

### 阶段 C：SkillEngine 与三个内置 Skill（进行中）

- Router 只选定 `skill_id`；普通单轮问答选择 `direct_answer`，缺少关键输入选择 `clarify`，复杂、多意图、需工具或多步执行选择 `orchestrator_plan`；高置信单一业务 Skill 进入准入评估。独立的顶层 ReAct 工具路由已移除；Plan 工具任务由受限 ReAct 子 Agent 循环处理。
- 实现业务 Skill 执行前准入评估及执行后过程/结果评估；评估器不代替 Policy Engine、输出 schema 校验或 Plan Verifier。
- 注册 `direct_answer`、`clarify`、`orchestrator_plan` 三个内置 Skill，内置算子与 Runtime 契约保持不变；`direct_answer` 负责单轮无工具回答，`clarify` 生成必要的澄清问题，`orchestrator_plan` 处理复杂或需要执行的任务。
- 为 `orchestrator_plan` 建立 Executor 插件，负责 DAG、Blackboard、Verifier 和有上限的 Replanner；工具任务按目标检索工具 allowlist 后交给 ReAct 子 Agent 在任务内循环执行，Markdown Skill 由 DeepAgents 执行，不另建顶层 ReAct 路由。
- 业务 Skill 通过 YAML steps 执行，可按已声明的 Skill ID 委派子 Skill 或调用工具；声明副作用等级，限制递归深度、循环次数和总预算。
- 应用随包提供代码审查业务 Skill；`llm.complete` 是无工具能力的只读 step action，其 Token 纳入运行预算。
- Plan 兜底接收路由/执行评估失败原因和候选 Skill；先匹配 Skill 能力，再将未覆盖目标拆分为工具任务，按任务检索工具子集。前次执行结果不确定时先检查状态，避免重复副作用。
- Plan 任务开始、完成、失败、中断和复用状态已通过脱敏 `plan.task` Trace 事件持久化；该诊断记录不包含任务正文、结果、参数或句柄，也不提供自动恢复。完整持久快照和恢复语义仍待设计，见 [ADR-0033](decisions/0033-plan-task-lifecycle-trace.md)。
- 验证每次 Skill 调用的上下文快照、失败/取消回滚、输出 schema 和临时 Blackboard 生命周期；全局预算熔断后不得继续自动规划。
- 使用脱敏业务集评估双路召回、路由裁判置信度和当前 top1 `0.85` / margin `0.30` Gate；只有评测支持时才提出阈值调整，评测结果不替代策略门控。
- 使用评测专用 Skill 与内存 Tool Executor 替身完成隔离的 Runtime 链路评测；越界路径拒绝、审批和工具调用验证不得触碰真实文件或服务。
- 对默认 Chat API 执行端到端联调，覆盖模型错误映射、聊天历史、Skill 分流、工具治理、流式配置和预算记录；用例与判分方案见[Stage C 评测计划](stage-c-model-evaluation.md)。

**当前进度：**`YamlSkillRegistry` 解析并校验 YAML 契约；Runtime 已实现三个 builtin Skill 注册契约、`direct_answer` 单轮 LLM 执行器、`clarify`，并把业务 Skill 的 YAML steps 依赖编译为 LangGraph DAG，允许独立节点并行执行（未安装可选 LangGraph 时由顺序 runner 按依赖调度）。同时具备输入/输出 Schema 校验、模型化准入与结果评估、评估 Token 预算记录、副作用快照和安全升级边界。默认 Chat API 已装配顶层选择器和 `SkillEngineAgentAdapter`，所选 Skill 会持久化聊天历史，并透传预算与审批 Provider。随包代码审查 Skill 通过只读 `llm.complete` action 分析用户提供的代码；YAML MCP action 和 Plan 工具任务通过现有治理 Provider 执行。Plan Planner 只接收 Skill 能力摘要与工具检索能力；工具执行子任务会从授权目录中按目标默认检索最多五个工具，候选数和工具子 Agent 轮数由 Harness 配置，Validator 和 Tool Executor 继续校验实际 ID。顶层选择、Planner、Verifier 和 Skill 评估统一使用普通 Chat 请求生成 JSON 文本，再由本地 Pydantic 校验；不依赖供应商的结构化输出 API。Plan 核心按依赖 wave 并行调度就绪任务，默认并发上限为 4；后续 wave 等待前置 wave 完成，失败 wave 会收集已运行任务结果后再决定重规划。运行时保留 Blackboard、有限重规划、失败任务拦截、完成任务复用和预算熔断；并发审批提示串行等待，避免 WebSocket 多协程同时读取消息。取消协议已为既有文件覆盖提供受治理补偿：默认 `write_text_file` 仍不可逆；新增 `write_existing_text_file` 通过进程内快照和条件恢复支持可逆操作。in-flight 补偿须由 handler 显式声明安全，恢复操作仍受预算、策略和用户审批约束，文件发生并发修改时转人工复核。取消摘要已持久化，且不存储 handle 或文件内容；快照只在进程内可用于实际恢复。远程 MCP 调用没有可信的自动补偿契约，仍按不可逆处理。2026-11-25 已通过 Chat/API 确定性集成验证覆盖 YAML/Markdown 格式分流、Skill 失败后转 Plan、工具候选授权、共享 RequestContext/预算、ToolFacade/ToolExecutor 与 WebSocket 高影响审批；三项集成用例通过。真实模型路由及 Plan 生成质量评测仍待用户在本机手动运行；route-only 基线不能替代该评测，详见[集成验证报告](test-results/2026-11-25-stage-c-runtime-integration-validation.md)。

ToolExecutor 边界另以 0 模型 Token 的静态探针验证：工作区外路径和缺失审批均在 handler 前被拒绝；批准的写入只进入内存 stub，未创建磁盘文件。详见[工具边界补充验证](test-results/2026-11-04-stage-c-runtime-boundary-supplement.md)。

语义消解使用纯规则结合最近任务焦点/会话摘要，不为查询改写调用模型；缺少唯一指代对象时在检索前澄清。业务 Skill 候选使用字段化 BM25 和 description 向量召回，经 1:1 RRF 融合、可选 Reranker 重排，再由轻量 LLM 选择并给出置信度；未配置或调用失败时使用主 LLM。唯一高置信 Gate 固定为 top-1 `>=0.85` 且 margin `>=0.30`，业务缺槽由 SkillEngine 在任何工作流步骤前澄清。召回功能和降级边界已有路由单测；当前下一步是使用本机活跃 Runtime 建立新方案的 Recall@K、MRR、Top-1、Gate 及延迟/Token 基线。

定向 Runtime 报告复核发现会议纪要测试夹具漏配 input mapping，已补 `$ref` 映射；Plan 模型漏输出 `input.tool_id`，已在 Planner/Replanner 契约提示中明确工具任务字段，并补充回归断言。修正后的两例真实模型复测尚未运行，详情见[诊断复核](test-results/2026-11-05-stage-c-runtime-diagnostic-review.md)。

### 阶段 D：统一模型 Provider、MCP Tool Facade 与记忆控制器

- **模型 Provider 首轮已实现：**`hostpilot.models` 定义规范化请求、响应、工具调用、Token 用量和分类错误；OpenAI 兼容 Chat API 适配器覆盖路由选择、单轮回答、Plan、Skill 评估、`llm.complete` 和 DeepAgents/LangGraph Agent 图，并提供超时与取消处理。Agent 图通过 LangChain ChatModel 接口转发至统一 Provider，LangChain 只承担框架集成。其他 LLM 接入类型、健康检查、降级策略和 Provider 配置选择仍待实现。验证见[Stage D Provider 验证](test-results/2026-11-06-stage-d-model-provider.md)。
- 后续按 LLM 接入类型增加适配器（如供应商原生 API、自托管推理服务），并明确各类的超时、健康检查和降级策略。具体供应商、服务和 SDK 在实现前评估，不预设特定推理引擎。
- **统一工具 Facade 首轮已实现：**提供工具描述目录、稳定 tool ID 调用和文件补偿委托；聊天路由、YAML MCP action、Plan 与工具目录均使用该接口，MCP Provider 负责内置进程内、本地 STDIO 和远程 Streamable HTTP 连接。所有调用仍经过现有 Tool Executor 治理链。新增只读连接状态接口，可报告各 Server 状态、传输类型、发现工具数量和脱敏错误码；远程工具动态路由表已独立抽取为 `MCPToolRoutingTable`，支持显式注入、批量冲突校验和调用来源验证。验证见[动态路由表实现记录](test-results/2026-11-08-mcp-tool-routing-table.md)、[Stage D Facade 与记忆控制器记录](test-results/2026-11-06-stage-d-facade-memory-controller.md)和[连接状态验证](test-results/2026-11-07-stage-d-mcp-connection-status.md)。
- **工具能力与隔离边界目录已实现：**工具页显示传输方式、信任等级、能力范围、运行位置、逐次审批及 OS 沙箱状态；元数据随工具注册，并对动态 MCP 明示 Server 自定义范围和本地/远端边界。验证见[工具目录记录](test-results/2026-11-10-tool-permission-catalog.md)。
- **Memory Controller 首轮已实现：**ContextBuilder 入口和 Plan 子任务通过 Controller 读取当前会话摘要和共享长期记忆，并按总字符预算截断；召回按 Markdown 标题／段落切片，用本地 BM25 风格词项评分排序，未命中片段按会话摘要优先、共享记忆其次回退。该机制不调用模型、不创建向量索引；同义改写仍可能没有词项匹配并退回固定顺序。archive 提案现由本地确定性规则评估，低分提案直接丢弃，中分提案在人工审批后保存抽取式摘要和原文 JSON 归档，高分提案在审批后保存原文。评分不调用模型，凭证样式内容会被降为低分。9 条离线合成查询、220 字符预算下，固定顺序、BM25 命中项和命中优先加回退的 Recall@budget 分别为 66.7%、88.9%、100%；MRR 分别为 0.291、0.722、0.759。混合策略平均返回 4.67 个片段，接近固定顺序的 5 个；这些小样本结果不代表真实用户记忆质量。完整报告见[评测记录](test-results/2026-11-08-memory-retrieval-evaluation.md)。评分和召回策略分别见 [ADR-0027](decisions/0027-memory-importance-tiers.md) 与 [ADR-0028](decisions/0028-hybrid-memory-recall.md)；长期仍需用用户认可样例校准评分、摘要、排序和去重策略。Trace 元数据已保存至有保留上限的 SQLite，并提供本地查询 API 和首版观测页面。
- 保留当前 SQLite 会话检查点与 Markdown 存储，直到持久记忆检索的存储选型明确。向量数据库不是本阶段的既定依赖。
- **混合召回评测已补齐：**离线合成集比较固定顺序、BM25 命中项与命中优先回退策略；小样本下混合策略 Recall@budget 100%、MRR 0.759，回退片段数接近固定顺序。详细指标和限制见[补充评测](test-results/2026-11-08-hybrid-memory-retrieval-evaluation.md)，不将其解释为语义召回效果。
- Trace 已增加 Skill、工具和审批事件 Span，并通过 `parent_span_id` 串起请求→Skill→工具→审批；页面显示 Span 与父级 ID。事件字段经白名单筛选，仍不保存对话正文、工具参数或审批摘要。Audit 事件独立写入有界 SQLite 留存，并可按 trace ID 和时间范围只读查询、分页及导出公式安全 CSV；Cost 持久化和导出流程仍待实现。

### 阶段 E：本地浏览器 Harness 管理界面

**当前进度（2026-11-22）：**项目看板、聊天、模型/Harness 配置、工具目录、MCP、Skill、Trace 和 Audit 页面均已接入主导航；配置、管理与观测流程已交付。现有组件/API 测试及 Playwright 主流程、审批流程均有覆盖；模型配置弹窗文字拖选误关闭已修复。2026-11-21 调整推理模型连接探针的 Token 上限和超时，并增加脱敏错误类别展示；用户已确认连接测试恢复正常。缓存审批卡片现展示历史成功子任务的安全工具执行摘要；旧记录提示摘要未知，重新执行提示可能再次产生副作用。Stage E 仅剩带桌面会话的 Tk 文件夹选择器手工验收，包括选择、取消和无桌面环境错误处理。

- 已交付 MCP 连接管理页面，可创建、编辑、启停和删除本地 STDIO 与远程 Streamable HTTP Server；支持导入标准 `mcpServers` JSON 中的 `command`/`args` 或 `url` 配置，含凭证字段或凭证型 args 的 JSON 会被拒绝并要求单独配置密钥。连接状态、发现工具数与脱敏错误码和配置元数据一起展示。环境变量管理的 Server 只读展示。
- 已交付 Skill 库只读首版：`GET /api/v1/skills` 聚合用户 YAML/`SKILL.md` 包和随包 YAML Skill，只返回通过校验的安全元数据及无效包数量；Vue 页面支持刷新和加载/错误状态，不返回本地路径或 Skill 正文。
- 已增加 Skill 详情只读查看：通过目录 Skill ID 读取经过注册表校验的 YAML manifest、`SKILL.md` 和文本资源；请求不接受文件系统路径。
- 已增加 Skill ZIP 导入：压缩文件和解压内容均有大小/数量上限，拒绝路径穿越、符号链接、可执行/二进制文件、无效包及重复 Skill；校验通过后原子安装到用户 Skill 目录。
- 已增加用户 Skill 启停与删除：YAML 格式更新契约中的 `meta.enabled`，Markdown 格式使用隐藏状态标记并由 DeepAgents 适配器跳过；随包内置 Skill 不可修改或删除，删除前浏览器要求确认。
- 已交付 Trace 观测：聊天路由/Token 用量、记忆召回、Skill、工具和审批事件按请求 ID 关联并写入 SQLite；页面提供请求列表和 Span 父子关系，属性白名单及 30 天/100,000 事件上限见 [ADR-0017](decisions/0017-local-trace-storage-and-query.md)。Audit 提供 90 天/100,000 事件本地留存、trace/时间筛选、offset 分页、只读页面与最多 1,000 条公式安全 CSV 导出。模型内部步骤和 Cost 留存仍未覆盖。
- 已交付通用 Harness 配置：上下文历史和记忆字符限制、请求/响应字符限制、Token/工具/知识检索预算及请求超时可通过页面管理，写入本地 SQLite，重启恢复并即时作用于后续聊天请求。Plan 最大子任务数（默认 10）、工具子 Agent 最大轮数（默认 5）、每个工具任务候选数（默认 5）、Planner 可见的历史成功结果数量和字符预算（默认 32 条/65,536 字符），以及路由业务置信度、Plan 候选阈值和候选数量也可在 Harness 页面配置；这些参数只改变有界运行时行为，不放宽授权、Schema、Policy 或审批边界。
- 模型设置已统一为一个配置入口，角色顺序为 LLM Model、轻量 LLM Model、Embedding Model、Reranker Model；主 LLM 是唯一必需项，可选模型按降级策略回退，模型设置存入 SQLite，Embedding/Reranker API Key 也本地持久化，主 LLM/轻量 LLM Key 只驻留进程内存。
- 已增加聊天工作区原生目录选择：所选路径存入本地 SQLite 并跨重启恢复；目录读取权限不足时保留现有工作区，目录可读不可写时显示只读状态并在工具参数校验前拒绝所有文件写入。权限探测创建并清理唯一临时文件，不修改目录权限、不提权。
- **工作区文件发现与分段读取已增加：**内置工具支持列出目录直接子项、在有限深度内按文件名 glob 搜索，并按 UTF-8 字节范围读取大文件；列表/搜索跳过符号链接，设置扫描、层数和结果上限，所有路径沿用会话三级权限。验证见[文件工具记录](test-results/2026-11-10-workspace-file-tools.md)。
- 已增加会话侧栏删除：浏览器二次确认后调用现有删除 API，清理会话索引、LangGraph 检查点、会话摘要和待审批记忆提案；删除当前会话后切换到最近会话，没有其他会话时显示空白会话。Trace/Audit 按其保留策略管理。
- 已交付会话级单向沙箱权限：新会话默认 read-only；用户确认后可升级到 workspace-write 或 danger-full-access，并持久化、写入 Audit；旧会话迁移到 read-only。ChatService 在 Session 租约内读取持久模式，写入请求级 RequestContext，并由 ToolFacade 作用域串行绑定/清理 Provider；FastAPI 不再递归修改 Runtime 对象图。`builtin.sandbox-backends` 另负责 Runtime Host 内共享后端集合。danger-full-access 当前只扩大内置文件工具的本机文件路径范围，不代表 shell、网络或 MCP 全权限。
- 已增加跨页面 UI 验收流程：从主导航进入 Harness、修改并重载已保存配置，切换 MCP 参数/JSON 配置模式，并访问 Skill、Trace 和 Audit 页面；Vitest 模拟 API 流程与 Playwright 真实浏览器/本地 FastAPI 联动 E2E 分层验证。E2E 使用独立临时数据目录，当前主套件 7 项通过，另有审批专用流程 1 项通过。
- MCP 连接页管理多个本地 STDIO 或远程 Streamable HTTP Server，显示每个 Server 的连接状态和动态发现工具；内置 Server 显示为 HostPilot 本地工具。
- Skill 包管理不编辑 Skill steps 或业务 Prompt；启停和删除仅作用于用户目录下通过校验的包。
- 模型/MCP 凭证不返回浏览器；Embedding/Reranker Key 本地持久化，主 LLM、轻量 LLM 与 MCP Key 在每次进程重启后重新输入。OAuth 等其他认证类型和配置导入导出仍需设计。

### 阶段 F：安全隔离与运行可靠性

- **Skill JSON Schema 首轮加固已实现：**输入/输出 Schema 设有节点和嵌套深度上限，只允许包内自引用，不解析网络或文件系统外部 `$ref`。用户上传的 Skill 包不能借 Schema 声明触发外部资源解析。
- **子 Skill 授权首轮已实现：**业务 Skill 在 `dependencies.skills` 中显式声明可委派目标；父 Skill 的 Plan fallback 默认拒绝未声明的子 Skill，顶层内置 Planner 仍可从启用目录中选择。Planner 提示与 Plan Validator 使用同一 allowlist，并在执行前重校验。
- **YAML Skill 工具 allowlist 首轮已实现：**MCP 工具 action 必须列入 `dependencies.tools`，注册表预检和 Runtime 均拒绝未声明的 action；业务 Skill 的 Plan fallback 由 Task Executor 仅从父 Skill 已声明且仍注册的工具中检索，并在执行前重校验。Planner 只看到抽象的工具检索能力；顶层 builtin Plan 的执行阶段可使用当前注册工具，空列表默认拒绝。
- **远程 MCP 参数 Schema 首轮已实现：**Server 返回的 inputSchema 在连接发现时按节点/深度上限校验，仅允许有效本地 JSON Pointer 引用；调用参数由 Tool Executor 在策略和审批前校验。Plan 与 YAML Skill 走相同执行链。
- **STDIO 凭证参数隔离已加固：**后端保存入口和页面表单/JSON 导入会拒绝 `--api-key`、token、Authorization 等凭证型 args；启动时清除旧配置中的匹配参数并停用对应 Server，防止敏感值继续进入子进程或配置 API；凭证仍通过单独字段提交并仅存于进程内存。
- **MCP 超时与取消清理首轮已实现：**本地工具发现最多等待 15 秒；远程连接、工具发现、LangChain 适配按每个 Server 的 timeout 设限；工具调用继续受 Server timeout 和请求预算共同限制；启动取消时关闭当前及已打开的 Server 会话，单次关闭最多等待 5 秒。
- **有限重规划与 Audit 关联已核验：**Plan 最多重规划 2 次；相同失败任务无新增信息时不会重复执行，已完成的等价任务会复用结果；MCP 工具调用没有自动重试。ToolExecutor Audit 事件带请求 `trace_id`，Audit/Trace 只记录工具 ID、状态和原因码，不记录原始参数。
- **真实远程 MCP 超时语义已验证并反馈给用户：**本机 FastMCP Streamable HTTP 集成测试证明，HostPilot 到时后停止等待，但 FastMCP 服务端工具任务继续执行，未收到取消信号。远端超时通过专用错误码在聊天 UI 提示“结果未知”，要求先检查目标系统状态；不会自动重试或声称操作已停止。当前行为证据限定于 FastMCP 2.14.7，其他服务端行为未验证，详见[测试记录](test-results/2026-11-09-remote-mcp-transport-cancellation.md)。
- **共享模型预算已加固：**Agent、Skill Selector、YAML `llm.complete`、Plan Planner/Verifier/Reasoning task 共用一次模型请求封装；每次调用串行获取请求级锁、在 Provider 前检查预算并限制输出 Token，用量返回后立即记账。真实用量超额会锁存预算耗尽状态，排队模型调用和新工具调用在启动前被拒绝；已由 Provider 记账的响应不再由 Runtime/ChatService 重复扣除。并行预算回归覆盖 YAML 与 Plan：超额后排队模型调用不发出，依赖 wave 的后续步骤或工具任务不执行。
- 会话沙箱状态已进入 SQLite 会话 schema；Linux 上六个内置文件工具经 Bubblewrap 执行，read-only/workspace-write 仅挂载所选工作区，danger-full-access 按次挂载当前目标；无文件沙箱支持的平台 fail-closed。权限升级严格单向并写 Audit；Linux 脚本沙箱和 MCP 工具能力沙箱使用独立边界。请求级模式绑定由 Runtime ChatService/ToolFacade scope 管理，后端对象集合仍由 builtin.sandbox-backends 持有。
- **Linux 多语言脚本沙箱已实现：**Python、JavaScript 和 POSIX Shell 分别用独立运行时与资源策略在 bubblewrap/prlimit 内执行；无网络、无宿主工作区挂载和继承环境，Shell 最多 16 个进程、Node.js 只读挂载运行时目录。工具目录分别探测运行时，不能启动对应隔离环境时禁用，实际执行失败 fail closed。三种工具均需要逐次审批；不隔离 STDIO MCP 子进程、进程内插件或远程 MCP。边界见[评估记录](evaluations/2026-11-09-sandbox-boundary.md)，测试见[验证报告](test-results/2026-11-10-script-sandboxes.md)。
- **平台与 STDIO 沙箱方向已修订：**当前只开发 Linux Agent，Windows Agent 后续独立开发。STDIO MCP 按 HostPilot 配置的工具能力 profile 从受限 Server 进程池分配实例；不同 profile 不共享进程，实例并行承载符合该 profile 的工具调用。工具能力由 HostPilot 授权，Server 实例的 OS profile 决定实际文件、网络、凭证和资源边界。详见 [ADR-0030](decisions/0030-cross-platform-code-and-mcp-sandbox-boundaries.md)；先前的 Windows/STDIO 候选评估保留为历史分析。
- **真实模型预算冒烟验证已通过：**一个 OpenAI 兼容服务在 12,000 预算下完成 DeepAgents 代码审查请求（报告 8,371 Token）；6,000 预算下返回 HTTP 429，Trace 确认在 DeepAgents Skill 执行阶段预算熔断。探测期间工具与知识检索预算为 0，结束后恢复 Harness 原配置及 YAML 执行模式。详见[报告](test-results/2026-11-07-live-agent-budget-smoke.md)。其他 Provider 及 Plan/YAML 混合流程的真实服务验证仍待进行。
- **Plan 与 YAML 真实模型冒烟已完成：**Plan 在 12,000 Token、2 次只读工具预算下完成两次 `read_text_file` 调用，总用量 3,380 Token；内置 YAML 代码审查在 6,000 Token、工具/检索预算为 0 时完成，总用量 3,257 Token；三根并行 YAML 模型步骤在当前 20,000 Token 配置下完成，总用量 17,933 Token。临时 Skill、会话均已清理。详见[验证报告](test-results/2026-11-08-live-plan-yaml-budget-smoke.md)。
- 已补充单次 Plan 请求委派并行 YAML Skill 的共享预算测试：Planner 与并行 YAML 步骤共用同一预算锁，Provider 超额报告后不再启动排队步骤，Plan Verifier/重规划不会继续调用模型。确定性验证见[Plan/YAML 混合预算报告](test-results/2026-11-08-plan-yaml-mixed-budget.md)。已发出的并发非模型操作不能通过事后 Token 用量回滚，需按其既有取消和补偿契约收敛。
- 为工具能力 profile 建立资源档位，明确 CPU、内存/Swap、PID、挂载/写入容量、临时存储、网络、凭证、并发和超时；具体数值在测量代表性 STDIO Server 的启动与峰值资源后确定，不凭空预设。
- 基于 Linux Bubblewrap/prlimit 实现隔离的 STDIO Server 进程池；按 profile 隔离进程、路由并行调用，并在隔离不可用时拒绝执行。
- 为进程池补充集成和端到端验收，覆盖发现、能力授权、profile 分流、并行调用、取消清理、重启/故障恢复与工具目录状态展示。
- Linux Agent 完成后，单独规划 Windows Agent；当前不实现 Windows 原生沙箱或启动辅助程序。
- 远程 MCP 超时后的真实传输行为已在 FastMCP 2.14.7 集成验证并有专用用户提示；尚未覆盖其他 MCP Server 实现。继续对 Agent 循环、模型降级、预算熔断和会话恢复进行集成与端到端验证。

## 需要继续确认

- 在带桌面会话的开发机验收真实 Tk 文件夹对话框及取消/无桌面环境错误；Playwright 已覆盖 Harness/MCP 配置持久化、MCP 工具发现、Skill 启停/删除、Workspace 状态、会话删除、沙箱升级、Trace 详情，以及真实 ToolExecutor 的批准/拒绝/取消副作用。
- 为 Skill 执行入口并发切换补充集成评测；Skill、工具和审批 Span 已串入 Trace。
- 记忆控制器的向量召回是否需要向量存储，以及具体后端；现有 SQLite/Markdown 继续承担已交付职责。
- 需要支持的 LLM 接入类型、各类型配置字段及降级策略的用户可控范围。
- 模型/MCP 非敏感配置的持久化介质与权限方案；其他认证类型的秘密生命周期。
- Skill YAML 完整 schema、上下文回滚语义、团队 Skill 同步与来源校验规则。
- 准入/结果评估的模型或规则组合、置信阈值、评估预算，以及可回滚 Skill 的补偿协议。

## 阶段展示同步

项目阶段与待办变化时，同步更新 `frontend/src/data/roadmap.ts`。历史测试报告、优化记录和 ADR 记录当时的真实实现状态，不因目标架构调整而改写历史结果。
