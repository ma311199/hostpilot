# Runtime 执行循环实现设计

## 意图

实现上一阶段定义的 Runtime-native Model/Tool 执行循环，让 Runtime 负责模型调用、工具提案校验、工具调度、结果回填和完成条件；具体模型与工具仍由可替换服务提供。

## 当前情况

- `RuntimeExecutionRequest`/`RuntimeExecutionResult` 与 Model/Tool/Skill/Loop Ports 已建立，但当前没有循环实现。
- 生产模型/工具循环仍由 LangGraph 图驱动；模型 Provider 已有规范化 `ModelRequest/ModelResponse` 和共享预算封装，工具通过 `ToolFacade` 进入 Policy、确认和执行治理。
- `ContextSnapshot` 的历史消息和 LangGraph checkpoint 仍由现有 Chat Runtime/持久化适配器管理。

## 目标

- 新增可注入 `RuntimeModelPort` 与 `RuntimeToolPort` 的循环实现。
- 提供生产适配器：Model Provider 适配器调用共享预算封装；Tool Facade 适配器转发同一 RequestContext 的预算和审批能力。
- 每次模型请求前检查预算；每次工具调用只能使用请求声明且 allowlist 允许的工具；工具提案整批验证后再开始执行，避免无效提案批次先产生部分副作用。
- 将模型消息、工具提案与匹配 `tool_call_id` 的工具结果按序回填后续模型请求。
- 用 `max_steps` 限制模型轮数；达到上限后不再执行新提出的工具调用。
- 累计模型 Token 用量，并避免重复计入已经由 Provider 记账的响应。
- `asyncio.CancelledError` 继续传播；预算、模型、策略、审批和工具错误不伪装为成功。

## 状态与持久化决定

循环按请求状态运行：调用方传入截至本轮的规范化消息；循环只在内存中追加本轮模型/工具消息并返回增量。循环不读写 SessionStore 或 Checkpointer。现有 Chat/Session 适配器继续负责加载历史并在成功提交 Turn 时保存结果；以后替换持久化后端不需要改变循环。

## 非目标

- 本切片不把循环切为默认 Chat Runtime，也不改变 HTTP/WebSocket 和当前 LangGraph 执行路径。
- 不将 Skill 作为模型可调用工具；顶层 Skill 选择、SkillEngine、Plan fallback 和 DeepAgents 仍沿现有请求链运行。`RuntimeSkillPort` 留作后续编排扩展。
- 不改变 ToolFacade/ToolExecutor 授权、Sandbox、审批、补偿或远程调用语义。
- 不加入并行工具执行、重试、模型流式事件、持久化和 Session 恢复。

## 执行规则

1. 循环按 `max_steps` 最多请求模型这么多轮，每轮执行前检查共享预算。
2. 使用 `RuntimeExecutionRequest.tools` 提供给 Model Port；没有工具定义时不请求工具。
3. 收到模型响应后先把 Assistant 消息追加到本轮增量消息；若没有工具提案，要求存在非空文本并以 `COMPLETED` 返回。
4. 有工具提案时，先验证每个工具名在定义和 allowlist 中、参数为 Mapping、调用 ID 非空且本批唯一。整批验证失败时不调用 Tool Port。
5. 若当前已达到最大模型轮数，返回 `MAX_STEPS_REACHED`，不执行该轮新提案。
6. 依序调用 Tool Port；每个返回值以同一调用 ID 追加为 TOOL 消息。下一轮 Model Request 获得完整消息历史。
7. Model Port 适配器通过 `complete_with_budget` 限制输出 Token 并记录 Provider 用量。循环只为未标记已记账的模型响应补记 Token。

## 方案与取舍

1. 先实现无副作用的抽象循环，调用方各自处理预算和治理：代码较少，但多个调用方容易遗漏预算或安全检查。
2. **实现独立循环和显式 Model/Tool Port 适配器，复用共享预算封装和 ToolFacade；只返回消息增量，持久化仍归 Session/Chat 适配器。** 此方案让 Runtime 拥有真实调度逻辑，同时沿用已经验证的模型与工具治理实现。
3. 同时替换默认 LangGraph Chat Runtime 并迁移 checkpoint 消息格式：控制权迁移最彻底，但会把执行循环、存储兼容、提示词和用户可见行为的变化合并，超出本切片。

采用方案 2。当前 LangGraph 路径继续服务现有请求，Runtime 循环先作为可直接测试、可由后续 Chat Runtime 插件接入的实现。

## 验收

- 单模型终答正确返回并保留 ModelTokenUsage。
- 工具调用完成治理执行，工具结果按正确 call ID 回填，之后模型可继续并终答。
- 未知/越权/重复 ID/非对象参数在任何同批 Tool Port 调用前拒绝。
- 请求 Context 身份保持不变；预算耗尽和取消不会触发后续调用。
- 达到 max_steps 后不执行新提案；工具定义/消息不被改写或丢失。
- 全量 pytest、Ruff lint/format、mypy 和 `git diff --check` 通过。

## 后续

接入 Chat Runtime 插件前，另行实现 Runtime Skill/Plan action 编排、System/Memory Prompt 组装与 Session State/Checkpoint 适配；在迁移评测证明兼容后再切换默认 LangGraph 路径。
