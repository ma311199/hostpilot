# ADR-0012：业务 Skill 快速路径与 Plan 兜底

- 状态：已采纳；Stage C 执行链已部分实现并接入默认 Chat API
- 日期：2026-11-03
- 路由入口后由 [ADR-0029](0029-three-skill-pipelines.md) 修订：普通无工具问答由 `direct_answer` 处理，独立 ReAct 工具循环不再保留；本文业务 Skill 快速路径、评估门和副作用安全要求继续有效。
- 关联：[新架构文档](../新架构文档.md)、[需求基线](../requirements.md)、[路线图](../roadmap.md)、[ADR-0009](0009-skill-runtime-and-yaml-contract.md)、[ADR-0011](0011-yaml-skill-registry-and-legacy-migration.md)

## 背景

新架构增加了单一业务 Skill 的快速执行路径：明确命中业务 Skill 时先评估适用性，再由 SkillEngine 执行 YAML workflow，之后评估执行过程与结果。必要时把执行快照交给 `orchestrator_plan` 继续规划。这修订了 ADR-0009 中“需要步骤或工具的请求一律由 Plan 接管”的边界。

## 决定

### 路由与入口分类

- Router 仍只选一个 `skill_id`，不执行 Skill，也不授予权限。
- 普通单轮问答选择 `builtin/direct_answer`；缺少关键输入时选择 `builtin/clarify`。
- 高置信度命中一个业务 Skill，且该 Skill 能覆盖请求中的**全部目标和动作**时，才可进入快速路径；只覆盖部分目标、多意图、低置信或复杂的请求进入 `builtin/orchestrator_plan`。用户显式要求规划时也进入 Plan。分类模型给出的完整覆盖声明只是候选判断，不能单独作为完成证明。
- “无业务 Skill 命中”本身不等于必须进入 Plan；简单问答仍由 `direct_answer` 回答，信息不足由 `clarify` 处理。
- Plan 内可以把业务 Skill 作为 `business_skill_task` 调用；这与顶层路由直接进入业务 Skill 快速路径是两种不同入口。

### 业务 Skill 评估门

- 执行前准入评估只判断 Skill 是否适用、必要输入是否齐备、请求是否多意图或复杂；不做全局任务分解，也不覆盖 Policy Engine 的工具授权。
- 输入缺失转 `clarify`；Skill 不适用、多意图或复杂请求转 `orchestrator_plan`；只有准入通过后才执行 workflow。
- 执行后评估检查过程合规性与结果是否覆盖用户目标；应逐项核对请求中的全部目标，不能因 Skill 自身步骤完成就判定请求完成。它不替代输出 schema 校验、Tool Executor 结果验证或 Plan 内的 Verifier。
- 评估接口应返回判定、置信度、理由和可关联的 Trace。评估器实现方式、阈值和预算在 Stage C 实现前细化；评估不可信或超时不得绕过安全策略。

### 副作用与兜底

- 每个 YAML Skill 必须声明 `side_effect_level`：`read_only`、`idempotent`、`reversible` 或 `irreversible`。缺失或未知值按无效契约处理。
- 执行快照至少记录 Skill ID 与 manifest hash/version、完成和失败的 step、已产生结果、工具/子 Skill 调用、策略决定、副作用记录、幂等键、失败原因及预算消耗。
- 仅只读或幂等执行可以在结果评估失败后自动进入 Plan。可回滚执行只有在已验证补偿路径后才允许自动兜底；不可逆操作不自动重跑，转澄清、人工或明确的补偿流程。
- Plan、Validator 和 RePlanner 都必须读取快照，跳过已完成步骤并避免无新信息地重调失败 Skill。工具操作仍由 Policy Engine、预算、确认门和 Tool Executor 治理。
- Skill 局部预算耗尽后，只有请求全局剩余预算足够才可进入 Plan；全局预算熔断后立即停止自动执行并返回可识别状态。
- RePlanner 有次数、时间、Token 和工具预算上限，并检测重复计划和调用环；达到上限后停止自动执行。

## 影响

- `skill.yaml` schema v1 增加必填 `side_effect_level`；Stage B 注册表拒绝缺失或未知值。
- Stage C 已实现结构化入口分类、模型化准入/结果评估、执行快照、副作用安全升级边界及有界 Plan 核心；默认 Chat API、MCP action、Plan 工具与子 Skill 适配已装配。模型评估纳入请求 Token 预算，但不替代确定性 schema、权限或工具结果校验。Plan 按依赖 wave 并行调度就绪 task，默认并发上限为 4。取消补偿注册表已支持宿主显式注册 handler，并在取消异常快照中保留补偿结果；默认 MCP action 尚无补偿器，取消快照也未持久化。
- Stage C 测试必须覆盖评估通过/拒绝/超时、快照完整性、重复副作用拦截、补偿门、全局预算熔断及有限重规划。
- 快速路径回归必须覆盖单一目标和多目标请求；当 Skill 只覆盖多目标中的一部分，或必需副作用信息缺失时，必须转 Plan 或澄清，不得因分类模型自报“完整覆盖”而接受部分结果。2026-11-16 `plan-004` 真实模型运行曾违反此要求，覆盖门当前标记为未解决，详见[测试记录](../test-results/2026-11-16-stage-c-plan-004-runtime.md)。
- Selector 当前对显式连接的多个动作执行保守 Plan 分流；这只是针对性保护，不替代运行时对完整用户目标的核验。`plan-004` 真实模型端到端复测及更广泛覆盖回归完成前，不宣称完整覆盖门已验收。
- 这项决定取代 ADR-0009 中“需要执行步骤的请求统一进入 Plan”的表述；ADR-0009 的 Runtime、三个内置 Skill、YAML 执行契约及统一治理原则继续有效。

## 复核条件

在接入可能产生副作用的业务 Skill、实现执行后自动兜底之前，必须验证快照和幂等/补偿机制。若评估器误判导致成本、延迟或副作用风险超出预期，应关闭对应快速路径或收紧适用范围。
