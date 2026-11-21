# Runtime Chat 可选适配器验证

- 日期：2026-11-19
- 范围：新增 `builtin.runtime-chat` 可选 Chat Runtime；复用 Runtime Model/Tool 循环、Provider/ToolFacade 适配器与 LangGraph Checkpoint；保留 `builtin.deepagents` 默认实现。
- 环境：Python 3.11、pytest、Ruff、mypy。
- 外部模型评测：未运行；本轮以合成 Model/Tool 循环验证适配边界，不调用真实 Provider 或产生真实工具副作用。

## 实现行为

- `RuntimeSessionStatePort` 位于 `hostpilot.runtime`，定义规范化 `ModelMessage` 的会话加载和追加；LangGraph 专用适配器位于 orchestration 层，不让 Runtime Core 依赖框架。
- Checkpoint 适配保留 User/Assistant/System/Tool 角色、Assistant 工具提案和关联 `tool_call_id`。无法识别或无法完整还原的 checkpoint 消息会显式报错，不静默丢弃。
- Prompt Builder 限制历史条数、替换为本轮 System Prompt，并将会话摘要和长期记忆明确包裹为不可信参考内容。
- `RuntimeChatAgentRuntime` 使用同一 `RequestContext` 调用 `RuntimeExecutionLoop`；User 输入在模型执行前保存，Assistant 工具提案在分发前保存，每个 Tool 结果在完成后立即保存。取消、执行异常和 `MAX_STEPS_REACHED` 不写入已完成 Turn。
- 如果后续模型调用失败，重试时按工具名称和规范化参数查找中断 Turn：已有结果会复用，不会再次调用工具；若 checkpoint 只有工具提案而没有结果，则向模型提供“结果不确定、不得重试”的工具消息，并要求先让用户核实。
- 工具参数必须可规范化为 canonical JSON；不满足时在保存提案和执行工具前拒绝，避免留下无法恢复的 checkpoint 状态。
- `builtin.runtime-chat` 从现有 ToolDescriptor 构造模型工具定义，Tool 调用仍经 `ToolFacadeRuntimeAdapter`；DeepAgents Skill 继续委托现有 LangGraph Runtime，外层 SkillEngine/Plan 路径不变。
- 注册表新增可选插件，但 `Settings.chat_runtime_plugin` 默认仍是 `builtin.deepagents`，未切换生产默认路径。

## TDD 与失败记录

- State/Prompt 测试首轮因模块缺失而在收集阶段失败；实现后定向测试通过。
- 首轮状态适配测试用了非 UUID session_id，暴露了既有 `session_thread_config()` 的输入约束；改用真实 UUID fixture 后通过，不需改生产逻辑。
- Runtime Chat 插件注册测试在移除新插件注册时按预期因未知插件失败，注册恢复后通过。
- 边界测试发现 `max_history_messages=0` 会因 Python 的 `[-0:]` 保留全部历史；新增测试先失败，再修复切片逻辑。
- 新增测试发现旧 System Prompt 会作为历史消息再次传入；Builder 现过滤历史 System 消息，保证每轮只使用注入的当前 System Prompt。
- 首轮全仓静态检查发现导入顺序、格式及一条超长文本；已修复。全仓 mypy 首轮发现消息类型未缩窄和 Skill 委托返回 Any；增加输入检查及返回契约验证后通过。
- 工具重放回归测试首轮如预期失败：不可 JSON 序列化的参数在提案增量已保存后才抛出普通 `ValueError`。将规范化校验移入提案验证后，改为在任何状态写入或工具调用前抛出 `RuntimeToolProposalError`。
- 首轮全量测试有 1 项旧测试桩失败，因为它没有按新的增量持久化契约调用 `message_sink`；更新测试桩后通过。首轮 mypy 检查发现重放结果还可能为未知值，显式声明联合类型后通过。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Runtime Chat/State/Prompt/Plugin 定向 pytest | 17 passed |
| 相关 Runtime、Chat、Skill adapter 定向回归 | 61 passed |
| 全量 pytest | 544 passed，1 条 FastMCP/Authlib 弃用警告 |
| Ruff lint（`src tests scripts`） | 通过 |
| Ruff format（`src tests scripts`） | 218 个 Python 文件已格式化 |
| 全仓 mypy（`src/hostpilot`） | 123 个源码文件通过 |
| `git diff --check` | 通过 |

## 中断恢复与重复工具保护（2026-11-20）

| 检查 | 结果 |
| --- | --- |
| Runtime execution loop、Runtime Chat 与 Runtime 插件定向 pytest | 24 passed |
| 全量 pytest | 548 passed，1 条既有 FastMCP/Authlib 弃用警告 |
| Ruff lint（`src tests scripts`） | 通过 |
| Ruff format 检查（`src tests scripts`） | 218 个 Python 文件已格式化 |
| 全仓 mypy（`src/hostpilot`） | 123 个源码文件通过 |
| `git diff --check` | 通过 |

本次补充验证了已完成工具结果的复用、未知工具结果的重试阻断，以及无效参数在工具执行和 checkpoint 变更前拒绝。模型和工具均为测试端口，没有调用真实模型或造成真实外部副作用。该保护先落在目标迁移路径 `builtin.runtime-chat`；不为计划逐步替换的 `builtin.deepagents` 旧路径复制实现。当前配置默认值仍待迁移验证通过后切换。

## 统一 Skill 执行契约（2026-11-20）

- 新增 `SkillExecutor.execute(SkillExecutionRequest)` 规范。HostPilot YAML `SkillEngine` 与 Markdown `DeepAgentsSkillExecutionAdapter` 现在通过同一分流调用点执行；Skill 解析、格式选择、结果评估与 Plan fallback 仍由 HostPilot 管理。
- 两种格式继续使用不同适配器；Markdown 适配器把 `ChatAgentResponse` 归一化为 `SkillExecutionResult`，并仅在原响应未记 Token 预算时补记一次。执行器缺失会报告 `skill_executor_unavailable:<mode>` 并交给 Plan，不会换格式重试。
- 不改变持久化的 `yaml` / `deepagents` / `both` 设置；将格式选择与执行框架解耦仍是后续迁移项。
- Skill Runtime/Engine/DeepAgents 适配定向测试：41 passed；全量 pytest：552 passed，1 条既有 FastMCP/Authlib 弃用警告。Ruff lint/format、123 个源码文件 mypy 和 `git diff --check` 通过。
- 后续边界复核发现默认 Markdown Runtime 缺少 `respond_with_skill` 时被误报为普通执行异常，导致 Plan 收到不必要的“执行结果不确定”。新增回归测试先按预期失败，再通过专用 `SkillExecutorUnavailableError` 将该情况归类为执行前不可用；当时运行模式仍名为 `deepagents`，Plan 收到 `skill_executor_unavailable:deepagents` 且 `previous_execution_outcome_uncertain=false`。格式名迁移后对应名称为 `skill_executor_unavailable:markdown`。该测试单项 1 passed，并包含在上述 41 项和 552 项中。

## Skill 格式与执行器解耦（2026-11-20）

- 格式偏好值统一为 `yaml`、`markdown`、`both`。分类器用 `markdown_skill` 决策，Runtime 以 `markdown` 键解析注入的 SkillExecutor；当前执行器仍由 `DeepAgentsSkillExecutionAdapter` 实现，真实框架名称只留在实现层。
- SQLite 启动时在 `BEGIN IMMEDIATE` 锁内检查并迁移旧表，将所有 `deepagents` 偏好改写为 `markdown`，保留 Skill ID 和其他偏好。并发初始化回归测试覆盖多个 worker 同时创建同一配置表。
- API 对外只返回新值，兼容旧客户端发送 `deepagents` 并规范化为 `markdown`；Web 配置显示 `Markdown (SKILL.md)`，并提交 `markdown` 值。
- 初次全量验证发现格式名批量替换误改了 DeepAgents 第三方导入，导致 10 项失败和 mypy 无法解析依赖；修复时恢复框架导入名、保留格式命名后复测。
- 最终验证：后端 pytest 553 passed，1 条既有 FastMCP/Authlib 弃用警告；前端 Vitest 78 passed、ESLint、TypeScript 检查及生产构建通过；Ruff lint/format、全仓 mypy（123 个源码文件）和 `git diff --check` 通过。未调用真实模型或产生真实工具副作用。

## 当前边界与后续

- 本次没有真实模型质量、线上延迟或生产工具副作用评测；默认 LangGraph 路径没有切换。
- `builtin.runtime-chat` 仍复用 LangGraph 图作为 Checkpoint 状态适配器和 DeepAgents Skill 执行器，因此目前并未移除 LangGraph 依赖。
- Runtime Chat 现按工具提案和工具结果增量写入持久 Checkpoint，并在重试时复用已知结果或阻断结果未知的调用，降低后续模型失败造成重复副作用的风险。外部工具若已产生副作用、但进程在保存工具结果前崩溃，系统无法判定实际结果，会阻断重试并要求用户核实。真实模型、并发会话恢复和生产工具副作用仍需在 Runtime-native 目标路径上验证。
- 后续在 Runtime-native 路径完成端到端与恢复验证后，将它切为默认 Chat Runtime，再逐步移除旧 `builtin.deepagents` 控制器；不把迁移验证扩展成对旧实现重复开发同一保护。Markdown 格式可继续通过受治理适配器执行。
