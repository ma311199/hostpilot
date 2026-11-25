# Runtime Chat 装配插件设计

## 状态

待用户审阅。本文只定义装配边界，不包含实施步骤。

## 意图

把 Chat Runtime 的组合职责从 FastAPI lifespan 和 API 配置更新路径迁入 Runtime Host 管理的插件，使启动和配置刷新使用同一装配入口，同时保持现有 Chat Runtime、Skill、Plan、模型与工具契约。

## 当前状态

- `create_configured_chat_agent_runtime()` 位于 `hostpilot.orchestration.runtime`，同步创建 ModelRuntimeComponents、Skill Registry/Engine/Selector、Plan Executor，并通过 `ChatAgentRuntimeRegistry` 创建 `ChatAgentRuntime`。
- FastAPI 在启动、MCP 配置刷新和主模型配置更新等路径直接调用该工厂。API 负责构造候选 MCP Provider/ToolFacade、启动候选工具、组装聊天 Runtime，并在配置锁内切换候选 Provider 和 Agent 代际。
- `builtin.chat-execution` 通过 `AgentLifecycleManager` 管理当前 Chat Runtime 的代际租约和关闭；`builtin.state-services`、`builtin.application-stores`、`builtin.tool-governance`、`builtin.mcp-provider` 等插件已注册相应共享服务。
- `RuntimeContext` 目前提供 Service Registry 和生命周期管理器，不负责模型配置，也不保存请求或用户数据。

## 目标与边界

- 新增必需 Runtime 插件 `builtin.chat-runtime-assembly`，提供稳定服务 `hostpilot.chat-runtime-assembly`。
- 装配插件通过 Runtime 服务边界获取当前已由 Host 管理的共享依赖，并接收每次候选装配变化的显式输入，例如设置快照、候选 ToolFacade、候选工具集合和 MemoryController。
- 装配插件统一负责 Model、Skill、Plan 与 Chat Runtime 的组合，返回一个尚未发布的候选 `ChatAgentRuntime`；插件不直接切换活跃 Agent，也不修改 MCP Provider、数据库配置或 FastAPI 状态。
- API 保留配置读取/持久化、候选 Provider 创建、并发锁和多资源协调切换职责，并改为调用 `hostpilot.chat-runtime-assembly` 服务，而不是调用组合工厂。
- `builtin.chat-execution` 继续独占 Agent 代际发布、在途租约和关闭；候选替换仍通过其既有 `replace()` 入口完成。
- 不改变 HTTP/WebSocket 契约、Chat Runtime 插件 API、工具策略/审批/预算、Skill 格式选择、Plan 生命周期、模型凭证处理或恢复行为。
- 不在本次迁移中拆分成 Model、Skill、Plan 三个生命周期插件；也不处理 DeepAgents 私有状态恢复。

## 插件与调用契约

装配插件提供一个候选构建服务，概念形状如下：

```python
class ChatRuntimeAssembly:
    def build_candidate(self, request: ChatRuntimeAssemblyRequest) -> ChatAgentRuntime: ...
```

`ChatRuntimeAssemblyRequest` 是不可变的显式输入，至少包含：

- 本次装配采用的 `Settings` 快照；
- 候选工具 Provider/Facade 及对应的框架工具集合；
- 已保存的 Skill 执行模式；
- 当前 MemoryController（它目前由应用 lifespan 构造，尚未作为 Runtime 服务发布）。

已由 Runtime 插件注册的 Checkpointer、Trace、Cancellation 和稳定 ToolFacade 等共享依赖通过服务 ID 获取，不由 API 从对象图中查找。MemoryController、候选 ToolFacade/工具列表及本次设置等当前由应用或配置更新路径创建的值，通过请求显式传入。现有 ModelProvider、Plan 和 Chat Runtime 注册表扩展点作为装配插件的构造依赖注入；不在本次迁移中新增无必要的 Host 插件。

如果配置没有可用的主模型，API 保持当前行为：不请求候选装配。自定义 `chat_agent_runtime_factory` 仍作为显式测试/嵌入覆盖路径，不由默认装配插件接管。

## 生命周期与配置更新流程

1. RuntimeHost 按依赖顺序启动 state、application stores、tool governance、MCP provider、chat execution 与 chat assembly 插件；装配插件在 setup 阶段校验必需服务已注册，不在 Host 启动时隐式创建用户模型。
2. API 启动或配置更新时准备候选 Settings、候选 MCP Provider/ToolFacade 和工具集合。
3. API 通过 `hostpilot.chat-runtime-assembly` 构建候选 Chat Runtime。装配失败时，调用方按现有规则关闭未发布的候选 Provider/资源，并保留当前活动配置。
4. 候选全部构建成功后，API 在现有配置锁内完成持久化与运行时切换：发布候选 Chat Runtime 代际、替换 MCP Provider、绑定稳定 ToolFacade 并更新 API 可见状态。
5. `AgentLifecycleManager` 继续让在途 Turn 使用旧 Agent 直到租约释放，再由 Chat Execution 插件关闭退役代际。
6. Host 关闭时，Chat Execution 插件负责当前/退役 Agent 关闭；装配插件只释放自己实际持有的注册表或资源，不关闭由 Agent 代际管理器接管的已发布 Runtime。

## 兼容与迁移策略

- 第一阶段新增装配插件服务，并将启动及配置刷新调用改为该服务；组合逻辑先原样搬入，避免同时重构算法和职责。
- `create_configured_chat_agent_runtime()` 在迁移期间保留为兼容入口，委托给相同的组合实现，避免破坏已有测试、导入点或嵌入调用者；确认无内部调用后再另行决定是否移除。
- 插件注册和服务解析采用稳定 ID；缺少必需服务时 Host 启动或候选构建应以明确错误失败，不静默回退到 API 直连工厂。
- 现有多资源切换不是数据库与内存之间的分布式事务。本次保留现行候选先构建、成功后在锁内发布的顺序，并确保候选构造失败不会改变活动运行时；不扩大持久化事务保证。

## 验收标准

- 默认应用启动、MCP 配置刷新和主模型配置更新均通过 Runtime 注册的装配服务构建 Chat Runtime，不再由这些 API 路径直接调用 `create_configured_chat_agent_runtime()`。
- 装配插件只创建候选 Runtime；活跃代际切换和旧代关闭仍由 `builtin.chat-execution` 管理。
- 装配缺少依赖、模型构建失败或候选 Provider 启动失败时，不覆盖当前可用的 Agent/Provider；候选资源按现有关闭规则释放。
- 初始装配与两类配置刷新仍注入相同的 ToolFacade、模型/Plan/Skill 注册表、Memory、Trace 和 Cancellation 能力。
- 自定义 Runtime 工厂路径、无模型启动、配置保存失败回滚和在途 Turn 代际隔离保持既有语义。
- HTTP/WebSocket 行为、审批、预算、工具结果恢复与 Chat Runtime 选择不变。
- 文档更新 Runtime 插件依赖关系和迁移进度；实施后按项目质量要求执行相关 Python/前端验证并记录结果。

## 未纳入本设计

- DeepAgents 私有执行状态跨 Host 恢复。
- 模型、Skill、Plan 各自拆成独立 Host 插件。
- 将 MCP Provider 创建、设置持久化和多资源切换整体迁出 FastAPI。
- 改变 Agent/Provider 配置刷新并发模型或引入新的持久化事务机制。
