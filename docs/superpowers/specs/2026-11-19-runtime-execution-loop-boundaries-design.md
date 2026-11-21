# Runtime 执行循环边界设计

## 意图

为 Runtime 后续接管 `Context → Model → Tool/Skill → Context` 循环建立明确、可替换的请求与服务契约。当前 LangGraph 仍负责生产环境中的模型/工具循环，本切片先让 Runtime Core 定义循环输入输出和它依赖的 Model、Tool、Skill Port，避免在迁移前重复实现一套循环。

## 当前情况

- `LangGraphChatAgentRuntime` 调用框架图；图内的 Agent 负责模型请求与工具调用循环。
- 模型层已有供应商无关的 `ModelRequest`、`ModelResponse`、`ModelToolCall` 和 `ModelProvider`。
- 工具层已有 `ToolFacade`、`ToolInvocationCoordinator` 和策略先行的 `ToolExecutionScope`；工具执行受预算和可选审批 Provider 管控。
- `SkillEngineAgentAdapter` 先选择 Skill，再将请求传给 `SkillEngine`；Plan fallback 和 DeepAgents 是当前两种处理路径。
- `RequestContext` 已贯穿 Chat Agent 和 Skill 链路，但执行循环边界尚未进入 `hostpilot.runtime`。

## 目标

- 在 `hostpilot.runtime` 定义 Runtime 执行请求和结果，不把用户数据放入进程级 `RuntimeContext`。
- 通过结构化 Port 描述模型补全、工具提案执行和 Skill 执行依赖；每个请求级 Port 调用均接收同一个 `RequestContext`。
- 复用当前 Model 请求/响应契约，模型输出的工具调用继续视为不可信提案。
- 明确工具执行必须经过已治理的工具 Port，执行循环不得直接持有 MCP Client、ToolExecutor 或 Provider 内部 handler。
- 为未来可替换的循环实现定义统一的异步 `run()` 契约，并验证请求边界、工具 allowlist 和执行步数限制。
- 保持 HTTP/WebSocket、LangGraph、预算、审批、工具授权和持久化行为不变。

## 非目标

- 本切片不实现 Runtime-native ReAct/工具循环，也不改变 LangGraph 当前循环。
- 不改变模型 Provider、ToolFacade、ToolInvocationCoordinator 或 SkillEngine 的现有接口和实现。
- 不迁移 Agent/Session 状态机、SessionStore、Checkpointer、持久化恢复或后台任务生命周期。
- 不重新定义预算、策略、Sandbox、审批或副作用补偿语义。

## 方案与取舍

1. 现在整体用 Runtime 循环替代 LangGraph Agent 循环。Runtime 可最快取得控制权，但会同时改变工具定义转换、消息/checkpoint 状态、取消和模型 Provider 适配，超出本阶段并增加行为回归风险。
2. 在现有 LangGraph 外包一层 Runtime 循环。迁移面看起来小，但会形成两个互相委托的控制循环，Runtime 仍不能决定模型和工具步骤。
3. **分阶段先定义 Runtime-native 请求/结果及 Model、Tool、Skill Port 契约，当前生产调用继续使用 LangGraph；后续单独实现循环，并逐个切换 Agent/Skill 适配器。** 此方式不引入无功能的包装循环，先固定架构边界并使后续执行器可测试、可替换。

采用方案 3。用户已批准先推进执行循环边界与接口。

## 契约草案

- `RuntimeExecutionRequest`：一个 `RequestContext`、不可变消息序列、可用工具定义及稳定工具 ID allowlist、正整数 `max_steps`。
- `RuntimeExecutionResult`：终态、最终文本、追加的规范化消息、累计模型 Token 用量及实际完成的循环步数。
- `RuntimeModelPort`：接收请求上下文与 `ModelRequest`，返回规范化 `ModelResponse`。具体适配器负责沿用共享预算封装。
- `RuntimeToolPort`：接收请求上下文与一个不可信 `ModelToolCall`，经既有治理执行链执行并返回工具结果；只有 allowlist 内的工具可供循环调用。
- `RuntimeSkillPort`：接收请求上下文、Skill ID 和输入对象，返回 Runtime 中立的 Skill 结果；具体 SkillEngine/DeepAgents 适配器由插件提供。
- `RuntimeExecutionLoop.run(request)`：Runtime 的稳定调用面。生产实现后续注入三个 Port；本切片只定义契约，不注册或调用空实现。

## 验收

- 非空消息、正整数步数、唯一工具 ID 和 allowlist 子集得到本地校验。
- 请求、结果与 Port 在 Runtime 包中可导入，不因 import 触发具体 Provider、MCP 或 Agent 框架加载。
- 测试验证请求对象保留 RequestContext 身份，工具提案类型复用模型契约。
- 现有 Chat/Skill/LangGraph/Tool 执行行为无变化；全量后端测试和质量检查通过。

## 后续

后续实现 Runtime-native 循环：共享请求预算；拒绝未声明或 allowlist 外工具提案；每次工具调用经 Tool Port；结果以关联的工具消息回填；在完成、澄清、拒绝、取消或 `max_steps` 用尽时返回终态。接入前还需确定运行消息/检查点的 State Port 所有权。
