# Runtime RequestContext 契约设计

## 意图

为 Runtime 增加明确的请求级 Context 契约，开始落实 ADR-0034 中进程共享 `RuntimeContext` 与请求隔离数据之间的边界。后续执行循环和生命周期迁移可以复用该契约。

## 当前情况

`RuntimeContext` 目前保存进程共享的服务注册表、Host 停止信号和 Turn 生命周期管理器。聊天请求数据分散在 `ContextSnapshot`、`RequestBudget`、审批提供器以及 `AgentExecutionContext` 中；后者只在 LangGraph 模型调用时组装。

## 目标与范围

- 在 `hostpilot.runtime` 暴露请求级 `RequestContext` 数据契约，包含已有的 `ContextSnapshot`、请求预算和可选审批能力。
- 在现有 LangGraph 边界把该契约转换为当前工具调用使用的 Mapping 形状，保留 `snapshot`、`budget`、`confirmation_provider` 键。
- 保持 `RuntimeContext` 进程级职责不变，不把用户或会话数据放入共享对象。
- 保持 `AgentRuntime.respond`、Chat HTTP/WebSocket、工具治理和持久化行为兼容；不在本切片迁移整个 Chat/Skill/Tool 调用签名。

## 非目标

- 实现通用 `Context → Model → Tool/Skill` 执行循环。
- 新增 Agent/Session 状态机、持久化、检查点恢复或取消语义。
- 改变预算、授权、审批、Sandbox 或模型 Provider 行为。

## 实现方式与取舍

1. 仅把 LangGraph 私有 `AgentExecutionContext` 重命名或复制到 Runtime，其他职责不变：迁移面最小，但 Runtime 契约仍然绑定单一适配器形状。
2. 定义 Runtime `RequestContext` 数据对象，并由 Agent Runtime 适配到现有 LangGraph Mapping：建立稳定的 Runtime 契约，同时保留既有工具读取方式。**本切片采用此方式。**
3. 立即重构 Chat、Skill、Tool 的全部接口传递 `RequestContext`：最终统一度更高，但会把独立迁移域合并到一次改动中，超出本切片。

## 契约

在 `src/hostpilot/runtime/context.py` 增加请求级数据对象，字段为 `snapshot: ContextSnapshot`、`budget: RequestBudget`、`confirmation_provider: AsyncConfirmationProvider | None`。该对象本身不持有共享 Runtime 服务，也不创建预算或执行授权。LangGraph 适配器负责转换成兼容 Mapping；Runtime 外部调用接口在后续切片中再逐步改为传递该对象。

## 验证

- 单元测试验证契约保存请求快照、预算和可选审批提供器。
- 适配器测试验证转换后 Mapping 的键和值与现有行为一致。
- Runtime、Chat、Skill、Tool 的相关回归和全量 pytest；执行 Ruff、相关 mypy 与 `git diff --check`。

## 后续

后续迁移 ChatService 创建并传递此对象，再逐步统一 Skill/Tool 执行入口；通用执行循环仍单独设计和实施。
