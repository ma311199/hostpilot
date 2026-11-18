# 2026-11-18：Runtime Turn 生命周期与插件托管验证

## 范围

验证 Runtime Core 对进程内 Turn 的跟踪和取消，并复测此前接入的 MCP Provider / Chat Runtime 插件依赖和关闭顺序。Turn 数据只记录 ID、Session/Conversation ID、状态和时间，不包含请求正文。

## 实现与覆盖

- `RuntimeContext` 新增 `TurnLifecycleManager`；ChatService 在构建请求 Context 后登记 Turn，结束时记录 `completed`、`failed` 或 `cancelled`。
- 管理器支持按 Turn ID、Session/Conversation ID 取消活动任务；RuntimeHost 关闭时取消并等待活动 Turn 结束。
- 新增 `tests/unit/runtime/test_lifecycle.py`，验证终态记录、Session 取消、Host 停机取消；FastAPI lifespan 用例确认聊天请求接入 Turn 管理器。
- 定向回归命令覆盖 RuntimeHost、Turn、MCP Provider、Chat Runtime、MCP 配置重建与 FastAPI lifespan：**22 passed**。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**471 passed、1 failed**。唯一失败为 `test_chat_sandbox_mode_is_isolated_between_conversations`：工具沙箱内的文件写入无法写入临时工作区，返回 `verification_failed`。该用例未经过新的 Turn/MCP 生命周期路径。
- 默认 pytest 收集仍在两个同名模块 `tests/unit/contracts/test_models.py` 和 `tests/unit/routing/test_models.py` 处冲突，因此全量使用 `--import-mode=importlib`。
- Ruff、Runtime/Host/Chat/MCP Provider 相关源码的定向 mypy 检查和 `git diff --check` 通过。

## Runtime Tool Execution Scope

Runtime Tool Governance 工厂现提供 `ToolExecutionScope` 契约。默认实现将既有 `ToolExecutor` 私有封装在 Scope 内；MCP Provider 为每个 Registry 创建独立主 Scope 和补偿 Scope，并保留独立的 Provider 补偿 Registry。正常本地/远程调用、FastMCP 拦截器以及补偿执行都通过注入的 Scope。API Provider 替换测试验证候选 Provider 获取新 Scope，稳定 Tool Facade 不变。

- 治理与 MCP Provider 定向测试：`.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_governance.py tests/unit/tools/test_mcp.py`，**30 passed**。
- 治理、MCP 与 API 组合测试：**94 passed、1 failed**。唯一失败为 `tests/unit/api/test_app.py::test_chat_sandbox_mode_is_isolated_between_conversations`，隔离文件沙箱向 pytest 临时工作区写入时返回 `verification_failed`。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**488 passed、1 failed**；唯一失败同上。该错误发生于被测文件沙箱子进程写临时目录，不在 ToolExecutionScope 调用逻辑内。
- Ruff 检查与格式检查通过；`.venv/bin/mypy src/hostpilot/tools` 通过。Pytest 输出一条 FastMCP/Authlib 弃用警告。
- `git diff --check` 在文档更新后复核。

## Runtime Tool Invocation Coordinator

`ToolExecutorFactory` 现在也创建 provider-scoped `ToolInvocationCoordinator`，由它选择该 Provider 的主执行或补偿 Scope。MCP Provider 解析本地/远程路由并准备 transport callback，再委托协调器；它不再直接调用 Scope。远程超时仍由 Provider 转换为 `RemoteToolOutcomeUnknownError`。初始和候选 Provider 分别创建协调器，ToolFacade 保持稳定；Registry、Transport 和 Provider 资源关闭责任不变。

- 协调器契约测试先在缺少 `create_coordinator` 时失败，随后通过；Provider/API 路径测试先因缺少 `_tool_coordinator` 失败，接线后 **4 passed**。
- 治理、MCP、API 回归（排除已知沙箱失败）：**96 passed、1 deselected**。不排除时为 **96 passed、1 failed**，唯一失败为 `test_chat_sandbox_mode_is_isolated_between_conversations`，文件沙箱子进程写 pytest 临时工作区返回 `verification_failed`。
- Ruff 检查与格式检查通过；`.venv/bin/mypy src/hostpilot/tools` 通过。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**490 passed、1 failed**。唯一失败仍是 `test_chat_sandbox_mode_is_isolated_between_conversations`，文件沙箱隔离进程无法写 pytest 临时工作区，返回 `verification_failed`；本轮 coordinator 定向用例均通过。

## State/Checkpoint 服务契约接入

新增 `builtin.state-services`，通过 `hostpilot.conversation-store` 和 `hostpilot.conversation-checkpointer` 两个稳定服务 ID 注册现有持久化 Port。`builtin.chat-execution` 声明依赖 State 服务；ChatService 与 Agent Runtime 装配从 Runtime 服务注册表解析会话 Store 和 Checkpointer。SQLite 等后端仍由现有工厂创建，Checkpointer 仍由 FastAPI lifespan 打开和关闭；该步骤建立依赖注入边界，不代表 State 后端生命周期已迁入 Host。

- 新增 State services 插件单测，FastAPI lifespan 集成测试验证两个服务指向当前注入实例，并在 Host 关闭时注销。
- Runtime Host/Turn/State/MCP Provider、Chat Runtime 和相关 API 定向测试：**23 passed**。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**472 passed、1 failed**；唯一失败仍为文件沙箱隔离执行无法写临时工作区。
- Ruff、Runtime 与相关 Host 插件/Chat 源码的定向 mypy 检查通过。

## Checkpointer 生命周期迁入 Host

State 插件现在接收 `CheckpointerFactory` 返回的异步上下文管理器，在插件启动时进入上下文并注册实际 Checkpointer 服务；插件关闭时退出上下文。FastAPI lifespan 不再单独进入/退出 Checkpointer 上下文。ConversationStore 实例仍由 FastAPI 创建并注入插件；其后端实例生命周期暂不由 Host 管理。

- State 插件单测验证上下文 `__aenter__` / `__aexit__` 由 Host 生命周期触发，服务注册并在关闭后注销。
- 包含自定义 Persistence Store 工厂、会话删除时 Checkpointer 操作、RuntimeHost 应用集成和 MCP 配置重建的定向回归：**25 passed**。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**472 passed、1 failed**。唯一失败仍为前述文件沙箱写入用例，未涉及 Checkpointer。

## ConversationStore 生命周期迁入 Host

`builtin.state-services` 现在通过注入的 `ConversationStoreFactory` 创建 Store，在插件启动时初始化并注册服务；插件停止时调用实现提供的 `aclose`（如果有）。ConversationStore 与 Checkpointer 均由该插件管理生命周期，FastAPI lifespan 仅提供工厂，不再直接创建、初始化或关闭这两个对象。SQLite 数据库路径仍通过共享准备函数检查符号链接并建立私有目录，再由配置 Store 初始化。

- 定向回归覆盖 Runtime、State 插件、MCP、Chat、API lifespan、配置数据库路径和会话删除流程：**25 passed**。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**472 passed、1 failed**。唯一失败仍为 `test_chat_sandbox_mode_is_isolated_between_conversations`，隔离执行无法写入临时工作区并返回 `verification_failed`；该失败未经过 State 插件生命周期路径。
- 后续定向复核命令覆盖 `tests/unit/runtime`、State/MCP/Chat Runtime 插件及 `tests/unit/api/test_app.py`：**83 passed、1 failed**。唯一失败仍为上述文件沙箱用例，复现原因为隔离进程对临时工作区的写入失败；本轮未改动沙箱实现，因此不作为 Runtime 回归失败统计。
- Ruff、相关源码 mypy 与 `git diff --check` 通过。

## 配置 Store 生命周期迁入 Host

新增 `builtin.configuration-stores`，由注入工厂创建、初始化并注册模型配置、路由模型配置、MCP 配置、Harness 配置、Workspace 配置和 Skill Execution 六类 Store。FastAPI lifespan 现在先准备数据库路径并启动 Runtime Host，再从 Runtime 服务注册表读取配置并构造 MCP Provider/Chat Runtime；Host 包住整个应用 lifespan，因此后续启动阶段失败也会清理已启动插件。Host 停止时配置 Store 按创建顺序逆序关闭；Store 实现支持 `aclose` 或 `close` 时会调用对应方法。Trace、Audit、Cancellation 和 Memory Store 尚未迁入。

- 新增插件单测覆盖六个工厂的创建、初始化顺序、服务注册、逆序关闭，以及插件初始化失败后的资源清理。
- 配置 Store、State/MCP 插件与 API 集成定向回归：**66 passed、1 deselected**。唯一排除用例为已知文件沙箱临时工作区写入失败。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**474 passed、1 failed**。失败仍为 `test_chat_sandbox_mode_is_isolated_between_conversations`，隔离执行无法写临时工作区并返回 `verification_failed`；与 Runtime 配置 Store 生命周期无关。
- Ruff 通过；`mypy src/hostpilot/api/app.py src/hostpilot/orchestration/configuration_stores_host_plugin.py src/hostpilot/orchestration/state_services_host_plugin.py` 通过。全包 mypy 另有 `src/hostpilot/evaluation/fixtures.py:39` 缺少 `step` 注解的错误，未涉及本次修改。`git diff --check` 通过。

## Trace、Audit、Cancellation 与 Memory Store 接入 Host

新增 `builtin.application-stores`，由工厂创建 Trace、Audit、Cancellation 和 Memory Store。插件启动时初始化前三种 Store，注册四个稳定服务 ID；Memory Store 没有异步初始化契约，创建后直接注册。Host 停止时按逆序关闭支持 `aclose`/`close` 的实现。FastAPI Chat、Memory Controller、Audit Sink 和取消补偿现在从 Runtime 服务注册表获取这些 Store。

- 新增插件单测覆盖初始化顺序、同步/异步关闭、逆序清理、服务注册，以及初始化失败后的清理。
- 配置/应用 Store、State/MCP 插件和 API 集成回归：**68 passed、1 deselected**；排除的仍是已知文件沙箱临时目录写入失败用例。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**476 passed、1 failed**。唯一失败仍为 `test_chat_sandbox_mode_is_isolated_between_conversations`（隔离执行返回 `verification_failed`）。
- Ruff 和针对 API/Store 插件的 mypy 检查通过；全包 mypy 的 `evaluation/fixtures.py:39` 缺少注解问题仍存在。

## Routing Model Provider 生命周期迁入 Host

新增 `builtin.routing-models`，通过稳定服务 ID 注册 Provider 生命周期管理器。启动后由 API 注入已配置的 Judge/Embedding/Reranker Runtime；配置变更时替换当前 Runtime，并暂存旧实例，避免影响正在使用旧 Selector 的请求。Host 停止时按逆序关闭当前与退役 Runtime。主聊天模型客户端继续随 `builtin.chat-execution` 所持有的 AgentRuntime 一起关闭。单次连通性检查创建的临时 Provider 仍由该 API 请求路径即时关闭。

- 新增插件单测覆盖多次替换、当前/退役 Runtime 延迟关闭、关闭顺序和服务注销；API 生命周期测试确认路由模型生命周期管理器由 Host 注册。
- 配置/应用 Store、State/MCP/路由 Model Provider 插件及 API 回归：**69 passed、1 deselected**；排除项仍是已知文件沙箱失败用例。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**477 passed、1 failed**。唯一失败仍为文件沙箱隔离写临时工作区，返回 `verification_failed`。
- Ruff 和修改模块的定向 mypy 检查通过；`git diff --check` 通过。

## Runtime ToolFacade 服务注册与 Provider 替换

新增 `builtin.tool-governance`，在 Runtime 服务注册表中暴露稳定的 `hostpilot.tool-facade`。代理调用转发到当前 MCP Provider；Provider 替换只切换代理目标，MCP Provider 插件仍独自关闭 Provider。Chat Runtime 插件声明 Tool Governance 依赖；FastAPI ChatService、工具目录与配置刷新共享 Runtime 中的同一 facade。候选 Runtime 装配时继续使用候选 Provider 专属 facade，只有替换提交后才切换活动代理。

- TDD RED：新增插件测试在生产实现前因 `ToolGovernanceHostPlugin` 模块缺失而无法导入，确认缺失行为。
- 新增四个代理单测，覆盖调用与描述转发、替换 Provider 后 facade 身份稳定、Host 关闭解除绑定，以及可选补偿和沙箱模式转发；新增 FastAPI 集成用例覆盖 MCP Provider 刷新后 facade 身份稳定、服务注册和 Provider 所有权同步。
- 自审发现 MCP-only 刷新已有 ChatService 时仍会保留候选 facade。新增带已配置 Runtime 的回归断言；将旧赋值临时恢复后用例按预期失败，改为从 Runtime 服务注册表解析稳定 facade 后通过。
- Tool Governance、Runtime 插件、API lifespan 与编排定向回归：**97 passed、1 deselected**。排除项仍是已知文件沙箱临时目录写入失败用例。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**482 passed、1 failed**。唯一失败为 `test_chat_sandbox_mode_is_isolated_between_conversations`，隔离执行无法写入临时工作区并返回 `verification_failed`；故障与本轮 Runtime ToolFacade 路径无关。
- Ruff lint、Ruff format 检查、API/ToolFacade 相关 mypy 检查和 `git diff --check` 通过。pytest 输出包含 FastMCP 依赖的 Authlib 弃用提示。
- Provider 替换顺序继续受现有 `runtime_lock` 保护；本轮未改变工具 Policy、预算、审批、Audit、Trace 或资源所有权。

## Runtime Sandbox Backend Injection

新增 `SandboxBackendSet` 和 `SandboxBackendFactory` 契约，并以 `LinuxSandboxBackendFactory` 继续创建现有 Linux 文件、Python、JavaScript 和 Shell 包装器。新建 `builtin.sandbox-backends` Runtime 插件，在 `hostpilot.sandbox-backends` 注册每个 RuntimeHost 创建的一组后端；MCP Provider 插件声明对它的硬依赖。Host 停止时服务会注销，插件释放集合引用。工厂构造不触发 Bubblewrap 主机探测；现有懒探测、按调用启动隔离进程、策略和错误行为保持不变。

`build_retrieval_mcp_provider()` 支持接收后端集合；不传集合时仍通过 Linux 默认工厂创建。FastAPI 提供可选 `sandbox_backend_factory` 注入，初始 Provider、MCP 配置刷新候选和 Model 配置刷新候选均复用 Host 注册的同一集合；每个 Provider 仍有独立 Workspace、Tool Registry、治理 Scope 和协调器。

- TDD RED：后端工厂测试在缺少导出接口时无法收集；Runtime 测试在插件不存在时失败；MCP Builder 注入测试在缺少 `sandbox_backends` 参数时失败；API 集成测试在 `create_app` 缺少工厂参数时失败。
- Runtime 插件、MCP Provider、MCP、Sandbox 和 Runtime Host 定向回归：**56 passed**。API 刷新测试确认工厂仅创建一次，三个 Provider 共用相同后端对象且 Workspace/Registry 不同。
- 包含 API、MCP Provider、Sandbox、Runtime 插件和工具治理的扩展定向回归：**125 passed、1 failed**。失败为下述长期已知的文件沙箱临时工作区写入失败。
- Ruff lint、Ruff format 检查通过；`.venv/bin/mypy src/hostpilot/sandbox src/hostpilot/orchestration src/hostpilot/tools src/hostpilot/api/app.py`：**Success: no issues found in 27 source files**。
- 全量 `.venv/bin/pytest -q --import-mode=importlib`：**497 passed、1 failed**。唯一失败为 `test_chat_sandbox_mode_is_isolated_between_conversations`：Bubblewrap 在 pytest 临时工作区执行写入时返回 `verification_failed`，确认审批后仍无法写入。该问题在本轮前的 Runtime 测试报告中已持续记录；本轮未改变 Sandbox Profile 或 Mount 规则。输出另有 FastMCP 依赖的 Authlib 弃用提示。
- 文档与代码白空格检查使用 `git diff --check`；它检查整个共享工作区的已跟踪差异，结果可能同时覆盖此前已有改动。

## Runtime ToolExecutor Factory

继续将工具治理的构造边界迁入 Runtime。`builtin.tool-governance` 现在同时注册稳定 facade 和 `hostpilot.tool-governance`；后者提供可替换的 `ToolExecutorFactory`。FastAPI 通过 `create_app(tool_executor_factory=...)` 注入工厂，供初始 Provider 和候选 Provider 创建主执行器与补偿执行器。Provider 仍持有自己的 ToolRegistry、Transport、处理器及执行器引用；直接调用 Provider builder 时使用同一默认工厂实现。

- TDD RED：工厂单测因 `hostpilot.tools.governance` 模块缺失而无法收集；Provider 注入用例在暂时绕过注入后按预期失败（工厂调用次数为 0）；API 集成用例在 `create_app` 未提供工厂参数时因未知参数失败。
- GREEN：新增默认工厂测试，验证 Registry 身份、Sandbox Policy、Audit 与 Trace 注入；Provider 用例验证独立主/补偿 Registry 及各自 Sandbox 限制；API 测试验证同一个 Runtime 工厂为初始与候选 Provider 创建执行器，并保持 ToolFacade 稳定。
- Tool Governance、Provider、MCP、Runtime 插件与 API 定向回归：**97 passed、1 deselected**；排除项为已知的文件沙箱临时工作区写入失败。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**485 passed、1 failed**。唯一失败仍是 `test_chat_sandbox_mode_is_isolated_between_conversations`：Bubblewrap 内无法写入临时工作区，Audit 原因为 `verification_failed`。该失败在本次 Runtime 改造前已存在。
- Ruff、Ruff format、修改模块 mypy 和 `git diff --check` 通过。pytest 有 FastMCP 依赖的 Authlib 弃用提示。
- 尚未迁移的是工具调用分发本身、Sandbox 生命周期与通用执行循环；本轮只将 PolicyEngine/ToolExecutor 的创建与替换实现注入 Runtime 服务。

## 限制与后续

- Turn 终态历史仅保留在当前进程的有界内存队列中；未实现 Session/Agent 状态机、State/Checkpoint 持久化、进程重启恢复或用户 API 取消端点。
- Runtime 尚未拥有通用 `Context → Model → Tool/Skill` 执行循环；工具调用分发与 Sandbox 生命周期仍需逐域迁入；当前 `builtin.tool-governance` 托管稳定 ToolFacade 并提供 PolicyEngine/ToolExecutor 工厂，但 Provider 仍执行具体调用。Model/Selector/Skill 的具体组装仍由 `create_configured_runtime()` 承担。
- 文件沙箱失败保留为独立待排查项，不作为 Runtime 生命周期测试通过的证据。
