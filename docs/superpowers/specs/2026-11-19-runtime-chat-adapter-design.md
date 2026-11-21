# Runtime Chat 接入设计

## 意图

让 Runtime-native Model/Tool 执行循环以可选 Chat Runtime 插件的形式承接聊天执行，同时保留现有 LangGraph 路径作为默认实现和回退点。目标是先验证 Runtime 与 Chat、Skill/Plan、Prompt/Memory、会话状态之间的边界，再依据回归结果决定是否切换默认实现。

## 当前情况

- `ChatService` 为每个 Turn 构造 `RequestContext`，通过 `ChatAgentRuntime` 调用聊天执行器。
- `SkillEngineAgentAdapter` 在 Chat Runtime 外层处理 Skill 选择、SkillEngine、DeepAgents Skill 和 Plan fallback。
- `LangGraphChatAgentRuntime` 负责当前模型/工具循环，并通过 Checkpointer 读取和保存会话状态。
- `DefaultRuntimeExecutionLoop` 已拥有有界 Model/Tool 调度，但只接收规范化消息并返回本轮消息增量，不负责 Prompt/Memory 或持久化。
- `ConversationMessage` 目前不保留 Tool call ID/工具提案，不能直接无损恢复 LangGraph 内部消息历史。

## 目标

- 新增独立、可选的 Runtime Chat 实现，通过现有 Chat Runtime 插件注册机制选择；本阶段不改变默认实现。
- 复用 `DefaultRuntimeExecutionLoop`、Provider/ToolFacade 适配器和同一个 `RequestContext`。
- 明确 System Prompt、对话历史和 Memory 的组装规则，并将 Memory 保持为不可信参考资料。
- 由独立 Session State Port 负责读取可用历史、记录完成的 User/Assistant Turn；执行循环本身不访问持久化。
- 保持 Skill 选择、SkillEngine、DeepAgents Skill 和 Plan fallback 的现有路径；不支持的能力必须明确拒绝或经既有回退处理，不得静默丢失。
- 先以隔离适配测试和行为回归验证，再评估默认路径迁移。

## 非目标

- 本阶段不删除或替换 LangGraph，不切换默认 Chat Runtime。
- 不迁移 Session/Agent 生命周期或 Checkpointer 后端。
- 不引入并行工具执行、流式事件、自动重试或新的审批策略。
- 不改变 HTTP/WebSocket 协议、预算、工具授权、Sandbox 和用户可见 Skill 路由。

## 方案与取舍

1. **一次性将默认 Chat Runtime 替换为 Runtime-native 实现。** 迁移直接，但会同时改变提示词、消息格式、Skill 调用、Checkpoint 和工具执行行为，回归范围过大。
2. 在 LangGraph 外增加 Runtime 循环包装。实现看似渐进，但形成两个控制循环，模型与工具步骤的责任仍不清楚。
3. **新增 opt-in Runtime Chat 插件，复用已经存在的 Runtime 执行循环；通过显式 Prompt/Memory 和 Session State 适配器接入，保留 LangGraph 默认实现。** 可以分开验证接口和行为，未覆盖的 Skill/状态能力在切换默认前补齐。

采用方案 3。此设计批准后，按“状态/消息边界、Prompt 与 Memory 组装、插件执行适配、完整行为回归”分步实施。每一步先增加失败测试；所有阶段完成前不切换默认路径。

## 关键设计

- 新 Chat Runtime 作为 `ChatAgentRuntime` 插件注册，通过构造参数注入 `RuntimeExecutionLoop`、Model/Tool Ports、Tool 定义及 Session State Port；不直接创建 Provider、MCP Client 或 Checkpointer。
- State Port 提供读取历史和提交已完成 Turn 的操作。Runtime 执行循环只消费本轮规范化输入消息、返回本轮增量；状态提交由 Chat Runtime 适配器负责。
- 在消息格式能无损表达 Tool call ID 与关联结果前，不将不完整的旧 Tool 消息拼入模型历史。实现阶段需明确历史转换、截断和终态提交规则，并以测试锁定。
- Prompt Builder 负责组合稳定 System Prompt、用户/助手历史和当前请求。Memory 内容包裹为不可信参考数据，沿用现有 `append_memory_context` 的安全语义。
- Skill/Plan 外层维持现有 `SkillEngineAgentAdapter` 行为。新 Chat Runtime 必须显式支持或将 DeepAgents Skill 路由到既有受支持实现；不得悄悄把 Skill 请求当作普通对话。
- 工具定义与 allowlist 来自当前请求授权的工具目录；执行必须通过 `ToolFacadeRuntimeAdapter`。未声明、越权及无效工具提案继续由 Runtime 批次校验拒绝。
- `MAX_STEPS_REACHED`、预算耗尽、取消和 Tool/Model 错误不能伪装成完成回复；消息状态不得在失败时被误提交为已完成 Turn。

## 验收

- 新 Runtime 可由插件 ID 选择，现有默认注册与默认行为不变。
- System Prompt、会话历史、Memory 和当前用户输入按约定组成模型输入；Memory 安全语义有测试覆盖。
- Runtime 执行使用原始 `RequestContext`，并通过已治理 Tool Port 调用工具。
- 成功终答只提交一次完整 Turn；预算失败、取消、异常和步数耗尽不产生伪成功记录。
- Skill、DeepAgents、Plan fallback 和 HTTP/WebSocket 行为回归通过；现有 LangGraph Runtime 回归不变。
- 全量测试、质量检查和架构/台账/验证报告同步完成后，才评估是否进行后续默认切换。
