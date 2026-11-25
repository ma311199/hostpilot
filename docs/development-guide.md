# 开发指南

## 开发流程

1. 开始模块实现前以[新架构文档](新架构文档.md)对齐需求、接口契约和模块边界；历史测试报告与 ADR 保留当时的实现状态，不替代当前架构说明。
2. 将外部框架与服务封装在适配边界内；先实现可替换接口，再接入具体供应方。
3. 每个行为变更补充对应测试；安全策略变更补充拒绝路径和边界测试。
4. 运行与改动相匹配的质量检查，并在变更说明中记录命令、结果和未覆盖范围。
5. 发现设计决策、测试结论或优化项时更新相关文档与决策记录。

## 环境准备

- 使用 Python 3.11 或更高版本与 uv；仓库以 .python-version 选择 Python 3.11，并通过 uv.lock 固定解析结果。
- Linux 用户运行文件及代码沙箱前，须安装 Bubblewrap 和 util-linux（提供 `prlimit`）；JavaScript 沙箱另需 Node.js。安装和命名空间排查见 [Linux 安装指南](linux-installation.md)。
- 在仓库根目录运行 uv sync --locked --python 3.11 --group dev 创建或同步虚拟环境并安装基础及开发依赖。
- 启用已选 Agent 框架组合：增加 --group agents；启用开发期 Gradio 原型：增加 --group prototype；两组可同时启用。
- 用户本地 Skill 按不可信内容处理；编辑后重新校验。高影响工具操作必须先完成策略授权，再取得用户明确确认。
- 更新依赖范围后运行 uv lock 并审查锁文件变更；CI 使用 uv sync --locked 保证环境与锁文件一致。
- 本地完整质量检查使用 scripts/check_quality.sh；CI 平台确定后可直接调用该脚本。

## Python 代码约定（建议基线）

- 遵循 PEP 8；使用类型标注描述公共接口和跨模块数据。
- 使用 snake_case 命名模块、函数和变量，PascalCase 命名类型，常量使用 UPPER_SNAKE_CASE。
- 类名按职责选用明确的角色词：`*Host`/`*Context`/`*Plugin` 专指进程级 Runtime Host 生命周期；聊天执行边界使用 `ChatAgentRuntime`；Skill 执行核心称 `SkillEngine`；模型创建结果称 `ModelRuntimeComponents`，其选择注册表称 `ModelProviderRegistry`。避免用裸 `Runtime` 表达多个领域职责。
- 同一实体的标识在 Python、JSON、WebSocket、TypeScript 和持久化层使用 `session_id`；TypeScript 局部变量使用 `sessionId`。保留 Conversation 作为资源名，因此资源路由仍为 `/conversations/{session_id}`。
- Protocol、Service、Adapter、Plugin、Store、Registry、Factory 等后缀仅用于对应角色；实现和适配器名称应包含被适配的领域对象，避免泛化的 `Runtime` 或 `Manager`。
- 公共类、函数和复杂安全逻辑应写清用途、约束、异常与副作用；注释解释原因，不复述代码。
- 避免模块循环依赖、重复策略实现和隐式全局状态；配置通过显式对象注入。
- 日志使用结构化字段，禁止记录密钥、个人会话敏感数据或不必要的完整提示词。
- 错误应使用可区分的类型或错误码；不要吞掉策略拒绝、超时和验证失败。

格式化与 lint、类型检查工具在 pyproject.toml 中使用 Ruff 和 mypy 配置；尚未建立 CI 自动化门禁。

## 配置与密钥

- configs/ 仅存放非敏感默认值和脱敏示例。
- 环境差异通过环境变量或部署配置注入；密钥由运行环境的 secret store 提供。
- OpenAI 兼容模型通过 HOSTPILOT_MODEL_NAME、HOSTPILOT_MODEL_API_KEY 配置；兼容服务可选 HOSTPILOT_MODEL_BASE_URL。接口流式请求默认开启，可用 HOSTPILOT_MODEL_STREAMING=false 关闭。密钥以 SecretStr 加载，模型名称、密钥和 Base URL 必须配套校验。
- 本地页面也可通过 /api/v1/model/configuration 配置 OpenAI 兼容模型；模型名称、Base URL 和流式选项保存在本地 SQLite。主 LLM 与轻量 LLM Key 只保存在当前后端进程内存，服务重启后需重新提交；Embedding 与 Reranker Key 保存在本地 SQLite 并在启动时恢复。状态接口不会返回密钥。
- 当前知识库 MCP 工具不访问外部服务，调用时返回“知识库暂未开启”。恢复真实检索时，通过 RAG_BASE_URL、RAG_RETRIEVAL_PATH、RAG_USER_ID 和 RAG_KEY 配置；RAG_KEY 以 SecretStr 加载，凭证不向模型或前端暴露，响应按 EvidenceSet 校验。
- MCP 连接由 Harness 连接管理面板管理。内置工具沿用进程内 FastMCP Client/Server；外部 MCP Server 支持本地 STDIO 子进程和独立 Streamable HTTP Client。Skill 只引用稳定的工具 ID，不依赖连接方式或命名空间。Server 可通过 `HOSTPILOT_MCP_SERVERS` 配置，也可在页面持久化非敏感连接设置；API Key 只保存在后端进程内存，重启后需重新输入。STDIO 命令不经过 Shell，子进程与 HostPilot 共享操作系统用户权限，不能视为沙箱。

```bash
HOSTPILOT_MCP_SERVERS='[{"name":"knowledge","url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer ${KNOWLEDGE_MCP_TOKEN}"},"timeout_seconds":15}]'
```

本地 STDIO Server 也可通过同一设置项配置，例如：

```bash
HOSTPILOT_MCP_SERVERS='[{"name":"filesystem","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/path/to/project"],"timeout_seconds":60}]'
```

STDIO 命令必须符合受支持的可执行程序 allowlist。`npx` 可能从 npm 下载并运行指定包；子进程使用与 HostPilot 相同的操作系统账户权限，不构成沙箱。内置 FastMCP 工具仍走进程内连接。

此配置示例描述环境变量方式，适合部署配置。不要把真实凭证写入仓库或普通配置文件；页面管理的非敏感 MCP 连接配置写入本地 SQLite，API Key 仅保存在进程内存，详见 [ADR-0014](decisions/0014-mcp-config-persistence-with-ephemeral-api-keys.md)。

页面管理的本地 STDIO 与远程 Streamable HTTP MCP Server 支持添加、编辑、启用/停用和删除。传输方式、command/args/cwd 或 endpoint、超时、启用状态和凭证名称等非敏感值跨重启持久化；`api_key` 由本地页面提交后仅保存在后端进程内存。应用重启后保留连接配置，需要密钥的 Server 会等待用户重新输入；读取接口只返回是否已配置。
- 请求入口可通过 HOSTPILOT_MAX_REQUEST_CHARS、HOSTPILOT_MAX_RESPONSE_CHARS、HOSTPILOT_REQUEST_TOKEN_BUDGET、HOSTPILOT_REQUEST_TOOL_CALL_BUDGET、HOSTPILOT_REQUEST_RETRIEVAL_CALL_BUDGET 和 HOSTPILOT_REQUEST_TIMEOUT_SECONDS 调整单请求限制；默认消息上限为 32,000 字符、响应上限为 100,000 字符，超时为 60 秒。模型提供方返回的 Token 用量会写入响应、请求预算和 Cost 事件。
- 新增配置项时记录类型、默认行为、敏感级别和校验规则。
- 模型/MCP 配置跨重启持久化；主 LLM、轻量 LLM 和 MCP 的 `api_key` 只使用进程内存并在重启后重新输入，Embedding 与 Reranker 的 `api_key` 写入本机 SQLite 以支持本地服务恢复。任何 API Key 都不得写入前端存储、构建变量或 Skill 包；读取 API 只返回配置状态。详见 ADR-0014 和 ADR-0015。

## 可替换 Plan 组件

Plan 的 Planner、Validator、任务执行器、Verifier 和 Replanner 可以独立替换，接口定义在 `hostpilot.skills.orchestration`。默认实现通过 `create_default_plan_component_registry()` 注册；自定义实现通过 `PlanComponentRegistry.register(role, implementation_id, factory)` 显式加入。

通过以下环境变量选择实现 ID：

```text
HOSTPILOT_PLAN_PLANNER_PLUGIN=builtin.llm
HOSTPILOT_PLAN_VALIDATOR_PLUGIN=builtin.runtime
HOSTPILOT_PLAN_TASK_EXECUTOR_PLUGIN=builtin.governed
HOSTPILOT_PLAN_VERIFIER_PLUGIN=builtin.llm
HOSTPILOT_PLAN_REPLANNER_PLUGIN=builtin.llm
```

自定义 Chat Runtime 通过 `chat_agent_runtime_registry` 注册，再将注册表传给 `create_app` 或兼容入口 `create_configured_chat_agent_runtime`。Chat Runtime 插件接口版本为 `2.0`；兼容工厂保留原导入路径和签名，并委托给 Runtime 装配使用的组合实现。插件可访问启动时注入的依赖，因此必须是受信任的 Python 代码并遵守 HostPilot 的策略、预算、审批、取消、补偿和审计契约；注册表不提供进程隔离。当前不支持任意模块路径导入或热加载。

应用集成代码可从默认注册表派生，并只替换需要的角色：

```python
registry = create_default_plan_component_registry()
registry.register(PlanComponentRole.PLANNER, "project.planner.v1", planner_factory)
settings = Settings(plan_planner_plugin="project.planner.v1")
app = create_app(settings, plan_component_registry=registry)
```

详见 [ADR-0018](decisions/0018-versioned-plan-component-plugins.md)。

## 可替换对话运行时

底层 Chat Agent Runtime 也通过版本 `2.0` 的 `ChatAgentRuntimeRegistry` 替换，默认实现为 `builtin.runtime-chat`。用 `HOSTPILOT_CHAT_RUNTIME_PLUGIN` 选择实现；需要兼容回退时可显式设置为 `builtin.deepagents`。自定义工厂接收模型适配器、checkpointer、Skill 适配器和工具列表，并返回实现 `ChatAgentRuntime` 接口的对象。

应用代码通过 `create_default_chat_agent_runtime_registry()` 获取默认注册表，注册自定义工厂后传给 `create_app(..., chat_agent_runtime_registry=registry)`。HostPilot 的 Skill 路由和外层适配器仍由应用组装。

详见 [ADR-0019](decisions/0019-versioned-chat-runtime-plugins.md)。

## 可替换模型 Provider

`ModelProviderRegistry` 按 Provider 实现 ID 注册 `ModelRuntimeComponents` 工厂，接口版本为 `1.0`，默认实现为 `builtin.openai-compatible`。使用 `HOSTPILOT_MODEL_PROVIDER_PLUGIN` 选择实现。工厂需返回统一 `ModelProvider` 和 Agent 框架模型适配器；`ModelProvider.complete` 必须遵守 HostPilot 请求/响应及 Token usage 契约。

应用可通过 `create_default_model_provider_registry()` 派生注册表并注册受信任的自定义工厂，再传给 `create_app(..., model_provider_registry=registry)`。工厂接收完整 Settings（包括 SecretStr API Key），必须遵守凭证生命周期；注册表不隔离 Python 代码。

详见 [ADR-0020](decisions/0020-versioned-model-provider-plugins.md)。

## 会话持久化 Port

会话元数据通过 `ConversationStore` Protocol 注入，LangGraph Checkpointer 使用单独的 `CheckpointerFactory`。默认 `create_app` 仍使用 `SQLiteConversationStore` 和 SQLite Checkpointer，它们继续共用同一数据库文件；调用方可分别注入 `conversation_store_factory` 与 `checkpointer_factory`。两者不要求通过插件 ID 选择。

自定义会话 Store 必须实现初始化、创建/查询/列表、活动更新时间、删除和沙箱级别单向升级。替换 Checkpointer 时还须支持会话 thread ID 的图状态生命周期；ID 映射使用 `session_thread_config`。

详见 [ADR-0021](decisions/0021-conversation-store-and-checkpointer-ports.md)。

模型和 MCP 配置持久化分别使用 `ModelConfigurationStore`、`RoutingModelConfigurationStore` 与 `MCPConfigurationStore` Protocol，可通过对应 factory 注入。主 LLM、轻量 LLM 和 MCP Key 不属于持久化契约；路由配置 Store 只持久化 Embedding 与 Reranker Key。

详见 [ADR-0022](decisions/0022-configuration-store-ports.md)。

Harness 请求参数、Workspace 路径及 Skill 执行模式偏好各自有独立 Store Protocol，并可通过对应 `*_store_factory` 注入 `create_app`。它们不共享宽泛的 JSON 存储契约。

详见 [ADR-0023](decisions/0023-harness-workspace-skill-preference-store-ports.md)。

Audit、Cancellation、Trace Store 分别实现 `AuditStore`、`CancellationStore`、`TraceStore`，通过 `audit_store_factory`、`cancellation_store_factory`、`trace_store_factory` 注入。默认继续使用既有 SQLite 表和数据库文件。任何替代实现都必须保持审计事件字段约束、Trace 安全属性白名单、CancellationSnapshot 脱敏，以及对应列表查询的输入限制。

详见 [ADR-0024](decisions/0024-audit-cancellation-trace-store-ports.md)。

会话摘要和长期记忆通过 `MemoryStore` Protocol 和 `memory_store_factory` 注入，默认使用 Markdown 文件存储。替代实现需遵守 `max_file_bytes`、会话 ID 隔离和单会话删除语义；只能由 MemoryWriteService 在审批和范围校验通过后写入。业务代码读取记忆应经过 MemoryController，不能直接访问底层 Store。

详见 [ADR-0025](decisions/0025-memory-store-port.md)。

远程 MCP 工具的动态名称映射由 `MCPToolRoutingTable` 管理，可通过 `MCPToolProvider(..., routing_table=...)` 注入实现 `RemoteToolRouteTable` Protocol 的路由表。新发现的映射必须在写入前整批校验名称冲突；调用阶段必须验证暴露 ID 与 Server/原生工具 ID 对应。路由表只决定目标连接，不替代 Tool Executor 的策略、预算、审批和结果校验。

详见 [ADR-0026](decisions/0026-mcp-tool-routing-table.md)。

记忆提案使用 `assess_memory_content` 本地确定性规则评估：0–39 为低分并立即丢弃，40–74 为中分，75–100 为高分。中分提案生成最多 800 字符的抽取式摘要，审批后摘要写入目标记忆文件、原文写入受限的 `archives/` JSON 文件；高分提案审批后将原文写入目标文件。当前仍要求显式审批后才持久化中/高分提案。凭证样式内容、运行日志、临时内容和单纯确认语句会得到低分；原文从不发给模型做评分或摘要。Markdown Store 最多保留 500 个中分原文归档，每个文件受 `max_file_bytes` 限制。

该评分器是可解释的首版启发式规则，不等同于语义价值模型；变更评分规则需补充正反例测试并更新 [ADR-0027](decisions/0027-memory-importance-tiers.md)。

MemoryController 召回时按 Markdown 标题和段落切成最多 1,200 字符的候选片段，以 BM25 风格词项评分优先注入查询命中的片段，再按会话摘要优先、共享长期记忆其次补入未命中片段。该规则没有语义理解能力；共享字符预算、会话隔离和不记录正文/查询的 Trace 行为保持不变。策略见 [ADR-0028](decisions/0028-hybrid-memory-recall.md)。

保留 `direct_answer`、`clarify`、`orchestrator_plan` 三个内置 Skill：普通无工具问答走 `direct_answer`，关键输入不足走 `clarify`，复杂或需工具/多步执行走 Plan。独立顶层 ReAct 工具路由不属于当前架构；Plan 工具任务内部可由受限 ReAct 子 Agent 多轮使用按任务检索的工具，且每次调用继续经过既有治理边界。路由原则见 [ADR-0029](decisions/0029-three-skill-pipelines.md)。

## 变更记录

每项显著优化记录问题、基准条件、修改、验证结果及回退方式。测试结果记录实际命令、通过/失败数量、覆盖率（若采集）和已知缺口。
