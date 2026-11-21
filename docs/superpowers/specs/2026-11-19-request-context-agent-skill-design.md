# RequestContext Agent 与 Skill 传递设计

## 意图

继续落实 Runtime 的请求隔离边界，让同一个 `RequestContext` 从 ChatService 进入 Chat Agent 与 Skill 处理链，避免在每层重复拆装快照、预算和审批能力。

## 当前情况

`ChatService` 已为每轮请求构建 `RequestContext`，但调用 `ChatAgentRuntime.respond()` 时重新拆成 `ContextSnapshot`、`RequestBudget` 和审批提供器。`SkillEngineAgentAdapter` 和 `SkillExecutionRequest` 也分别接收这些字段。LangGraph 运行时再将字段打包回现有工具 Mapping。

## 目标与约束

- `ChatAgentRuntime.respond(message, request_context)`、DeepAgents Skill 入口、Skill 选择器及 `SkillExecutionRequest` 接收同一请求对象。
- LangGraph 适配器使用同一对象构造现有 `snapshot`、`budget`、`confirmation_provider` 工具上下文，保留对象身份和键名。
- `RequestBudget` 必须是活动可变实例；可选审批 Provider 缺失时仍以 `None` 传到工具上下文。
- HTTP、WebSocket、ChatService 调用者和工具治理 Port 保持现有行为。工具/模型 Port 是刻意保留的适配边界，需要参数时从 `RequestContext` 读取字段。
- Chat Runtime 插件方法签名改变，插件 API 版本从 `1.0` 升为 `2.0`；旧版本注册应明确失败，不做隐式兼容。
- 不实现通用执行循环，不改变预算、路由、审批、持久化和工具授权语义。

## 方案

1. 在 ChatAgentRuntime、Skill Adapter、Selector 与 SkillExecutionRequest 之间传递一个 RequestContext，并在现有 LangGraph/Tool/Model Port 边界适配。此方案以一个对象贯穿当前请求链，并限定兼容拆包发生的位置，符合既定分阶段迁移方向。
2. 只改 ChatAgentRuntime 的参数，Skill 请求继续分散保存字段。改动较少，但会在 Agent 进入 Skill 时再次拆分，不解决用户提出的下游传递问题。
3. 一次性把所有模型和工具 Provider 的公开接口也改成 RequestContext。边界更统一，但超出本阶段，扩大对自定义工具和 Provider 的兼容影响。

采用方案 1。实现前后都保留外部 Chat HTTP/WebSocket 契约；替换式 Chat Runtime 插件通过显式 2.0 版本识别新签名。

## 验证

- 测试同一 RequestContext 实例通过 Agent Host Plugin、Skill Selector 和 SkillExecutionRequest 的身份保持。
- 测试无审批 Provider 时 LangGraph Mapping 仍含 `confirmation_provider: None`，预算对象不复制。
- 测试插件注册表接受 2.0、拒绝 1.0。
- 全量 Python 测试、Ruff、mypy 与 `git diff --check`。
