# ADR-0034：Runtime Core 与进程内插件生命周期

## 状态

已采纳，分阶段实施；Runtime Host 第一阶段已实现。

## 背景

当前项目已有 `ChatAgentRuntime`、`SkillEngine`、模型 Provider、Chat Agent Runtime 与 Plan 组件注册表，但它们属于不同层次。Runtime 的构造、服务装配、启动与关闭仍分散在 FastAPI lifespan 和 `create_configured_chat_agent_runtime()` 中；没有统一的 Runtime Core 创建共享上下文、按依赖加载模块、注册服务、管理后台任务和逆序清理资源。

HostPilot 的核心应是一个应用级 Runtime Host，各功能模块通过版本化插件契约接入。现阶段服务以 FastAPI 进程内运行，因此实例作用域是每个 ASGI worker 进程，而不是跨多进程的全局单例。

## 决定

- 后续新增独立 `hostpilot.runtime` 核心模块，提供 `RuntimeHost`、共享 `RuntimeContext`、请求级 `RequestContext`、插件契约、服务注册表和生命周期管理；API、CLI 等入口仅负责创建 Host、交付请求和关闭 Host。
- 每个应用进程通常创建一个 `RuntimeHost`。Host 复用 ASGI 主事件循环，不在 FastAPI 内另起一条常驻主循环；Host 负责监督自己的后台任务、就绪状态和停止信号。
- 区分两种上下文：进程级共享 `RuntimeContext` 提供非敏感配置视图、日志/Trace、停止信号和受控 Service Registry；凭证通过授权的 Provider 服务按需获取，不广播到所有插件。每次聊天、Plan 或工具请求另建 `RequestContext`，承载 conversation、ContextSnapshot、预算、取消和审批能力。请求数据不得写回进程级共享 Context。
- Runtime Core 管理 Agent、Session、Turn 与 Task 的生命周期：创建/绑定、调度、取消、状态转换、终结和恢复协调。具体 Agent 执行策略、Session 状态记录和 Task 执行器通过插件接口提供。
- Runtime Core 驱动通用执行循环：`Context → Model → Runtime 决策 → Tool/Skill → Context`。模型只返回文本或未受信任的调用提案；Runtime 检查 Schema、身份/能力授权、预算和策略，需要时取得明确审批，再交给注入的 Tool/Skill 执行服务。执行结果回填请求 Context 后继续循环，直到完成、澄清、拒绝、超时或取消。
- Runtime Core 定义状态语义及 `SessionStore`、`TaskStateStore`、`CheckpointStore` 等稳定 Port，管理状态转换、写入时机、恢复流程和版本兼容检查；SQLite、PostgreSQL、文件或远端数据库等具体介质、Schema、迁移和事务由可替换 Store 插件负责。Runtime 不规定长期状态的具体存储方案。
- Runtime Core 建立资源和隔离边界：为调用提供受限的预算、权限、资源 profile 和 Sandbox Port；实际 OS 沙箱、容器、进程池、配额实现由 Sandbox 插件提供。工具不能绕过 Runtime 的授权、预算、审批和结果验证链。
- Runtime Chat 是 Agent 执行的唯一控制面：可复用 DeepAgents、LangGraph 等框架提供的 Agent、DAG 调度和 Checkpoint 能力，但必须通过插件/适配器接入。Runtime Chat 决定何时调用、可用能力、预算、审批、取消、状态记录、恢复和生命周期；框架不自行绕过这些边界启动或重试执行。替换 `builtin.deepagents` 指替换旧 Chat Runtime 适配器，不要求移除仍符合契约且有价值的底层框架。
- 每个插件声明稳定 ID、契约版本、硬依赖、提供的服务和可选/必需级别。Runtime 在启动前校验缺失依赖、ID/版本冲突和依赖环，按拓扑顺序创建和启动插件，并在所有必需服务可用后报告 ready。
- 插件先向受限 Service Registry 注册服务，再启动外部资源。重复注册默认失败；依赖方只按服务契约获取依赖，不直接导入另一个插件的具体实现。
- 必需插件启动失败时 Runtime 启动失败，不进入 ready；可选插件失败时记录不可用状态并降级其依赖功能。插件为进程内可信 Python 代码，这些契约提供替换与故障边界，不提供安全隔离或崩溃隔离。
- 关闭时停止接收新请求，通知插件取消其后台任务，按启动顺序的逆序调用关闭钩子并释放资源。补偿仅由明确声明且受治理的补偿能力执行；Runtime 不会因为卸载插件而盲目撤销已发生的外部业务副作用。
- 模型供应商协议和凭证处理、数据库产品和持久化格式、MCP 连接方式、Sandbox 执行技术、具体 Skill/Plan 策略及 Web API 均不属于 Runtime Core 的实现职责；它们通过插件或入口适配器接入。
- 迁移采用兼容优先的阶段方式：先建立 Host 与上下文契约；再把现有默认 Chat/Model/Plan 实现包装为内置插件；随后逐域迁移 API lifespan 中的装配；每次只替换一个模块，并保持现有 HTTP/WebSocket 契约。

## 生命周期对象

| 对象 | Runtime Core 管理的内容 | 插件提供的内容 |
| --- | --- | --- |
| Runtime Host | 进程级启动、就绪、请求接入、停止与插件监督 | 插件实现和服务 |
| Agent | 创建/注册、能力引用、调用调度和关闭 | Agent 框架、Prompt/策略实现 |
| Session | 生命周期与状态转换、并发/取消/恢复协调 | Session Store、Checkpointer 实现 |
| Turn | 请求上下文、预算、取消、审批和完成状态 | 模型与工具调用服务 |
| Task | 调度、依赖状态、重试/恢复边界和结果归档 | Planner、Task Executor、Verifier |
| Sandbox | 能力授权、资源限额、创建/销毁时机 | OS/容器/进程隔离实现 |

单个 Runtime Host 的 Context 是进程共享服务上下文，不承载某个用户的会话内容；Session/Turn/Task 的运行数据必须通过请求上下文和对应 State Port 隔离。

## 目标模块边界

```text
src/hostpilot/runtime/
  host.py          RuntimeContext、RuntimeHost：启动、就绪、请求接入、关闭
  context.py       RequestContext
  lifecycle.py     Agent / Session / Turn / Task 状态机（当前仅实现 Turn 跟踪与取消）
  execution.py     Context → Model → Tool/Skill 执行循环
  ports.py         Model、Tool、State、Checkpoint、Sandbox 等契约
  plugins.py       插件契约、注册表、依赖排序
  services.py      按服务契约注册与解析

src/hostpilot/plugins/
  model/           ModelProvider 与框架模型适配器
  chat/            DeepAgents/LangGraph 等 ChatAgentRuntime 实现
  routing/         请求分类与 Skill 选择
  skills/          YAML / DeepAgents SkillEngine
  plan/            Planner、Validator、Task Executor、Verifier
  tools/           Tool Facade、Policy、Approval、MCP Provider
  state/           Session/Task Store 与 Checkpoint Store
  sandbox/         OS 进程、容器及资源 profile 实现
  memory/          Context / Memory 服务
  observability/   Trace、Audit 与 Cost
```

此树是目标边界，不要求一次性搬动所有现有源码。插件目录可继续使用各域的内部包；只有生命周期和服务契约统一由 Runtime Core 管理。

## 影响

- 替换模型、Agent 框架、路由和执行模块时，API 与 Runtime Core 契约保持稳定。
- 插件依赖关系和资源关闭顺序变得显式；必要插件损坏时可在服务接流量前失败。
- 现有 `ChatAgentRuntime` 与组件注册表作为迁移期适配器保留，不与新的应用级 Runtime Host 混为一个概念。
- 插件接口不会令未经信任的插件安全；第三方或不可信代码需要独立进程、权限和 IPC 设计。

## 实施进度

- 已新增 `hostpilot.runtime`，包含进程级 `RuntimeContext`、服务注册表、插件依赖排序、启动/就绪/关闭状态和插件后台任务取消管理。
- 新增请求级 `RequestContext`，聚合单次执行的 `ContextSnapshot`、活动 `RequestBudget` 和可选审批提供器；进程级 `RuntimeContext` 不保存这些请求数据。
- `ChatService.send()` 为每次聊天 Turn 创建 `RequestContext`，并将同一对象传入 ChatAgentRuntime、Skill 选择器和 Skill 执行请求；Skill 失败转 Plan 与 DeepAgents 结果评估继续沿用该对象。Chat Runtime 插件 API 升为 2.0，旧版 1.0 注册会被拒绝。
- LangGraph 适配器在工具上下文边界将 RequestContext 映射为现有 `snapshot`、`budget`、`confirmation_provider` 字段；可选审批提供器缺失时映射值保持 `None`。模型和工具 Port 暂保留现有调用形状，由适配边界读取 RequestContext 字段；route-only 评测行为不变。
- `hostpilot.runtime.execution` 已定义 `RuntimeExecutionRequest`/`RuntimeExecutionResult`、执行终态、Model/Tool/Skill Ports 和 `RuntimeExecutionLoop` 契约。请求包含同一 RequestContext、消息、工具定义、工具 ID allowlist 和正整数步数上限；实际循环实现及治理 Tool Port 适配见下一条。当前默认 Chat Runtime `builtin.runtime-chat` 使用 Runtime-native Model/Tool 循环和规范化 Session State Port；兼容实现仍可显式选择。默认切换决定见 [ADR-0035](0035-runtime-chat-default.md)。
- `DefaultRuntimeExecutionLoop` 已实现有界 Model/Tool 轮次：每轮通过 Model Port 请求，先完整验证一批不可信工具提案，再按序经 Tool Port 执行并以 `tool_call_id` 回填结果；共享预算、allowlist、步数上限、Token 用量及任务取消均有测试。`ModelProviderRuntimeAdapter` 使用 `complete_with_budget`，`ToolFacadeRuntimeAdapter` 继续经过当前 ToolFacade 治理链。循环采用请求消息输入和消息增量输出，不持有 SessionStore/Checkpointer。2026-11-21 起，默认 `builtin.runtime-chat` 的直接对话通过 Runtime 执行循环运行；Prompt/Memory 组装和 LangGraph Checkpoint State Adapter 已接入，YAML Skill 由 SkillEngine 执行，Markdown Skill 仍委托 DeepAgents/LangGraph 适配器。`builtin.deepagents` 保留为显式兼容插件。该路径复用 ModelProvider、ToolFacade 和请求预算；User、工具提案和工具结果按消息增量写入持久 Checkpoint，重试时复用已保存结果、对结果未知的提案阻断重复调用。真实模型评测、跨进程恢复、故障注入及 SQLite + Chat API 集成测试通过后，于 2026-11-21 切换默认；未在旧路径重复实现恢复逻辑。
- `RuntimeContext` 提供进程内 `TurnLifecycleManager`。ChatService 对每个已构建的请求 Context 登记 Turn 并记录完成、失败或取消；管理器支持按 Turn ID、Conversation/Session ID 和 Host 停机取消活动任务，并只保留有上限的进程内终态元数据。Turn 持久化和 Agent/Session 状态机尚未实现；Chat Runtime 的消息级 checkpoint 与工具结果恢复由 Checkpointer 适配器提供。
- FastAPI lifespan 当前创建一个 `RuntimeHost`；`builtin.mcp-provider` 管理 MCP Provider 的替换与关闭，`builtin.chat-execution` 声明依赖 MCP Provider 并持有、切换和关闭组合式 `ChatAgentRuntime`。停机时 Host 按依赖逆序先关闭聊天 Runtime，再关闭 MCP Provider。
- `builtin.configuration-stores` 在 Host 启动时通过注入工厂创建并初始化模型、路由模型、MCP、Harness、Workspace 和 Skill Execution 六类配置 Store；`builtin.application-stores` 创建并初始化 Trace、Audit、Cancellation Store，同时注册 Memory Store。两个插件注册稳定服务 ID，并在 Host 停止时逆序关闭支持 `aclose`/`close` 的实现。FastAPI lifespan 在 Host ready 后从服务注册表读取配置和 Store，再组装 Provider 和 Chat Agent Runtime；Host 启动包住整个 lifespan，因此后续配置读取或 Provider 构造失败也会触发清理。`builtin.state-services` 通过工厂创建并初始化 ConversationStore，通过 Checkpointer 工厂打开检查点上下文；Host 关闭时先退出 Checkpointer 上下文，再关闭支持 `aclose` 的 ConversationStore。`builtin.chat-execution` 通过关闭活动 ChatAgentRuntime 释放其组合式主模型客户端；`builtin.routing-models` 管理 Judge/Embedding/Reranker Provider 的替换与 Host 关闭，退役 Provider 保留到 Host 停止以避免影响在途请求。`builtin.tool-governance` 注册稳定的 `hostpilot.tool-facade` 与 `hostpilot.tool-governance` 服务：前者在 MCP Provider 替换提交后切换代理目标，后者为每个 Provider Registry 创建主/补偿 `ToolExecutionScope`，默认 Scope 内部封装 PolicyEngine/ToolExecutor；Provider 保有 Registry、Transport、调用路由和独立补偿 Registry，MCP Provider 插件继续负责 Provider 资源关闭。Policy 决策仍由 Scope 执行，工具调用分发仍发生在 Provider 路径内；Sandbox 生命周期尚未迁入插件。`create_configured_chat_agent_runtime()` 仍负责装配 Model、Selector、Skill 和 Plan。Agent/Session 生命周期和持久 Turn 状态尚未实现，后续逐域迁移。Runtime-native 循环已接入默认 Chat Runtime；当前状态见 [ADR-0035](0035-runtime-chat-default.md)。
- Runtime Tool Execution Scope 已补入 `ToolExecutionScope` 契约：Runtime 注入的 `ToolExecutorFactory` 为每个 MCP Provider Registry 创建独立 Scope；默认实现将现有 `ToolExecutor` 私有封装在 Scope 内。MCP Provider 的本地、远程、FastMCP 拦截和补偿调用均经过主/补偿 Scope，补偿 Registry 仍由 Provider 独立持有；Provider 不再依赖具体 ToolExecutor。此改动不改变策略、审批、预算、超时、审计/Trace、验证或补偿语义。
- Runtime Tool Execution Scope 验证：治理与 MCP Provider 定向测试 **30 项通过**；Ruff 检查、格式检查和 `src/hostpilot/tools` 定向 mypy 通过。治理、MCP 与 API 组合测试 **94 项通过、1 项失败**；全量 pytest 为 **488 项通过、1 项失败**。唯一失败 `test_chat_sandbox_mode_is_isolated_between_conversations` 在隔离文件沙箱向 pytest 临时工作区写入时返回 `verification_failed`，与本 Scope 改动无关的既有环境问题。详见[Runtime Turn 与 Tool Execution Scope 验证报告](../test-results/2026-11-18-runtime-turn-lifecycle.md)。
- Runtime Tool Invocation Coordinator 将主执行和补偿 Scope 的选择收敛到 Runtime 创建的 Provider-scoped 协调器。MCP Provider 提供已解析的工具 ID、参数和传输回调；路由、MCP Client、远端超时结果翻译、Registry 与资源清理仍属于 Provider。候选 Provider 使用独立协调器，不更换稳定 ToolFacade。
- `builtin.sandbox-backends` 通过 `hostpilot.sandbox-backends` 服务创建并持有每个 RuntimeHost 的一组文件、Python、JavaScript 和 Shell 后端。MCP Provider 声明依赖该插件，并在初始装配及 MCP/Model 配置刷新时注入同一组后端；Provider 继续持有独立 Workspace、Registry 和治理状态。默认 Linux 工厂只创建现有 bubblewrap 包装器，主机能力探测仍按需懒加载；每次 Sandbox 调用仍按次启动隔离进程，没有常驻 Sandbox 进程或额外 Runtime 主循环。直接调用 MCP Provider Builder 时仍默认选择 Linux 工厂。
- Sandbox 后端注入验证：Sandbox 后端与生命周期、MCP Builder、API 初始装配和两条 Provider 刷新路径的定向测试结果记录于[Runtime Turn 与 Sandbox 后端注入验证报告](../test-results/2026-11-18-runtime-turn-lifecycle.md)。
- Runtime Tool Invocation Coordinator 验证：治理/MCP/API 回归（排除已知沙箱失败）**96 项通过**；Ruff 检查/格式和 `src/hostpilot/tools` 定向 mypy 通过。全量测试 **490 项通过、1 项失败**，唯一失败仍是隔离文件沙箱写 pytest 临时目录的 `verification_failed`。详见[Runtime Turn 与工具调用协调器报告](../test-results/2026-11-18-runtime-turn-lifecycle.md)。
- 命名与会话 ID 后续统一：进程宿主继续使用 `RuntimeHost`/`RuntimeContext`；聊天执行契约为 `ChatAgentRuntime`，Skill 执行核心为 `SkillEngine`，模型组件与注册表为 `ModelRuntimeComponents`/`ModelProviderRegistry`。会话标识在 Python、HTTP、WebSocket、前端与 SQLite 中统一为 `session_id`，Conversation 资源名称和 `/conversations` 路径不变。SQLite schema v2 到 v3 原位重命名标识列，保留现有 UUID 与 LangGraph checkpoint thread ID。

## 验收标准

- 单个 ASGI worker 的应用生命周期内只有一个 Runtime Host；同一进程的聊天请求共享插件提供的服务，但各请求有独立 RequestContext 和预算。
- 插件按声明依赖顺序启动，缺失依赖和依赖环在处理请求前报告。
- 必需插件失败会阻止 ready；可选插件失败不会破坏无关功能。
- 停机时后台任务被取消，资源按依赖逆序释放，重复关闭安全。
- 现有聊天、Skill、Plan、工具审批与 HTTP/WebSocket API 行为在分阶段迁移中保持兼容。
