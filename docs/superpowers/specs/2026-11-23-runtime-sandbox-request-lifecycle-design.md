# Runtime Sandbox 请求级生命周期设计

## 意图

把会话 Sandbox 模式的绑定、传播和清理从 FastAPI 路由迁入 Runtime 执行边界，避免 API 递归遍历 Chat Runtime 和 MCP Provider 的内部对象来修改模式。目标是让 Chat、Plan/Skill 工具、PlanRun 恢复和 Chat Turn 恢复都在同一条请求作用域内使用会话保存的 Sandbox 模式，并确保失败、取消和审批断开后模式得到清理。

## 当前状态

- 每个 Runtime Host 已由 `builtin.sandbox-backends` 创建一组共享文件/Python/JavaScript/Shell 后端，并注入 MCP Provider。OS 隔离进程仍由现有后端按次启动。
- `ConversationSummary` 已持久化 `sandbox_mode`，但 `RequestContext` 没有该字段。
- FastAPI 在 REST 和 WebSocket Chat、PlanRun 恢复及 Chat Turn 恢复周围调用 `_set_runtime_sandbox_mode()`，递归查找对象属性并设置 mode；同时通过 `sandbox_mode_scope()` 设置 ContextVar。
- 递归设置是为了覆盖在持久 MCP Server task 中运行的本地文件工具：这类 task 不一定继承单次 Chat 请求的 ContextVar。请求结束时由路由执行反向清理。
- Chat 路由还持有 `model_components_lock`，用于隔离运行中的模型/Provider 与配置替换；该锁不属于本次迁移目标。

## 目标与边界

- Sandbox 模式是请求级执行授权数据，不进入模型提示词或进程级共享 `RuntimeContext`。
- `ChatService` 在 Session 租约内读取持久的会话模式，将其写入本次 `RequestContext`，并在全部工具执行路径外层进入稳定 `ToolFacade` 提供的 Runtime Sandbox 请求作用域。
- Sandbox 作用域只依赖 `ToolFacade` 明确暴露的绑定能力，不递归检查 Chat Runtime、Provider 或 MCP 对象属性；作用域在 `finally` 中恢复 ContextVar、Provider 模式和作用域锁。
- 保持 read-only、workspace-write、danger-full-access 的现有决策、审批和 OS Sandbox 行为不变；兼容 Runtime、MCP 本地持久任务和当前 Provider 替换路径。
- 保留现有 `model_components_lock` 的 Provider 重建保护。本阶段不改变 API/WS 消息契约、升级授权规则或远程 MCP 工具语义。
- 不重新设计为完全无共享可变状态的逐工具沙箱参数协议；这需要改造持久 MCP handler 的上下文传播契约，可作为后续独立设计。

## 考虑过的做法

1. **由 Runtime Chat Service 统一进入 ToolFacade Sandbox 请求作用域（推荐）**：在 `RequestContext` 保存会话模式，由 ChatService 覆盖整个 Turn/恢复操作，并让 Runtime 管理的 ToolFacade 对稳定 Provider 绑定模式、序列化作用域和清理。覆盖默认 Runtime、旧兼容 Runtime 及持久 MCP task，同时把 API 对象图遍历移除；保留现有串行语义。
2. **把 Sandbox 模式逐次传入每个工具调用和 MCP handler**：避免 Provider 上的会话模式可变字段，理论上可实现不同 Sandbox mode 的并发执行；但需要改造所有 Tool/Skill/Plan/DeepAgents 调用 Port 和持久 MCP handler 的请求上下文传播，范围显著更大，且需单独验证并行会话隔离。
3. **只把递归 helper 移到另一个插件**：代码改动较小，但生命周期仍由 API/插件外层按调用设置，RequestContext 不表达权限上下文，职责边界没有收敛，不作为目标方案。

## 建议的请求流程

1. REST/WebSocket 入口获取现有 Provider 配置锁；入口不解析或绑定 Sandbox 模式。
2. `ChatService` 持 Session 操作租约，读取 Conversation 的持久 `sandbox_mode`。
3. 构造新的 `RequestContext(sandbox_mode=...)`，并通过注入的稳定 `ToolFacade.sandbox_mode_scope(mode)` 进入 Sandbox 请求作用域。
4. ToolFacade 作用域持有专用异步锁，设置 ContextVar 并调用当前 Provider 的显式 `set_sandbox_mode(mode)`；MCP 持久 Server task 继续通过 Workspace 的显式模式属性读取模式。该锁防止独立 ChatService 请求互相覆盖，现有 Model/Provider 配置锁仍负责替换安全。
5. Runtime 执行普通 Chat、Plan/Skill 工具，或用户明确触发的 PlanRun/Chat Turn 恢复；逐工具策略和确认沿用既有路径。
6. 正常返回、异常、超时、取消或 WebSocket 断开时，`finally` 恢复 ContextVar 和 Provider 的默认模式并释放作用域锁。

## 验收标准

- ChatService 从存储的 Session 模式创建请求 Context；Sandbox 模式不进入模型可见消息。
- REST 和 WebSocket 的 Chat/恢复入口不再调用 `_set_runtime_sandbox_mode()` 或 `sandbox_mode_scope()`；`ChatAgentRuntimeHostPlugin` 和 FastAPI 对象图递归模式发现移除。
- 只读 Session 仍拒绝文件写入；可写 Session 仍按现有审批规则工作；危险模式仍按已确认升级后的规则工作。
- 工具失败、审批拒绝、请求取消和 Socket 断开后，下一条不同模式 Session 的请求不继承前一模式。
- 持久 MCP 本地工具 task 在请求模式作用域内使用正确模式；Provider 配置刷新及 MCP 重建测试继续通过。
- 同时触发不同 Session 的 ChatService 请求时，不会交叉使用 Sandbox 模式；序列化行为如需调整，另行设计并验证。
- Python 全量质量脚本及前端现有检查通过；不运行真实模型评测。

## 留给实现者

- 选择最小的稳定作用域接口与注册位置，避免 Sandbox 插件依赖 `builtin.tool-governance` 并与 `builtin.mcp-provider` 形成依赖环。
- 明确 Provider 切换期间的作用域锁与模式清理行为；更新已过时的 Runtime 架构说明中“Turn 元数据未持久化”描述时，只修正与本次接触部分直接相关的事实。
