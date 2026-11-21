# ChatService 使用 Runtime RequestContext 设计

## 意图

让 `ChatService` 为每个聊天 Turn 创建一个 Runtime `RequestContext`，并在 ChatService 的执行链内部将它作为单一请求载体传递，为之后逐步统一 Agent/Skill/Tool 边界做好准备。

## 当前情况

`ChatService.send()` 分别创建 `ContextSnapshot`、`RequestBudget`，并单独传递可选审批提供器；它们随后作为多个参数传入私有 `_execute_turn()`。Runtime 已定义 `RequestContext`，但目前仅由 `LangGraphAgentRuntime` 在适配边界创建。

## 目标与非目标

- 每个 `send()` 在 ContextBuilder 完成后创建一个 `RequestContext`，其中保存带当前预算快照的 `ContextSnapshot`、活动 `RequestBudget` 和审批能力。
- 让 `_execute_turn()` 接收并使用该对象，避免继续拆分传递这三个请求字段。
- 保持 `ChatService.send()`、`AgentRuntime.respond()`、Skill/Tool Port 及 HTTP/WebSocket 契约不变；在调用旧接口时仍传现有参数。
- 本步骤不要求 ChatService 与 LangGraph 最终共享同一个 RequestContext 实例；该适配链的接口迁移另行实施。
- 不实现通用执行循环、Session/Agent 生命周期、持久化恢复或取消语义变化。

## 方案与取舍

1. 现在将 AgentRuntime、Host 插件和 Skill 适配器一并改为接收 RequestContext：统一更彻底，但变更范围大，影响多个兼容边界。
2. **先在 ChatService 内创建并传递 RequestContext**：执行链内部逐步收敛，旧调用边界拆包，保持插件和 API 兼容。本步骤采用此方案。

## 验证

- 定向测试验证 Chat Turn 的快照、活动预算和审批提供器仍完整传至 AgentRuntime 与工具路径。
- Chat、Runtime、Skill 和工具相关回归及全量 pytest；执行 Ruff、相关 mypy 和 `git diff --check`。
- 文档同步 ADR-0034、架构文档、路线图、测试目录、指标台账和 Runtime 报告。
