# ADR-0029：按 Skill 格式执行并以 Plan 兜底

## 状态

已采纳；2026-11-14 补充 YAML / Markdown 分流及 Plan 兜底规则

## 背景

早期目标架构描述了通用 ReAct 工具循环。普通单轮问答和工具循环是不同能力：前者不调用工具，只需一次回答；后者会循环决策并使用工具。项目同时支持两种业务 Skill 包：`skill.yaml` 由 SkillEngine 执行，`SKILL.md` 由 DeepAgents 执行。路由需要明确两种格式的入口，并在执行或路由评估失败时安全地转交 Plan。

## 决定

- 保留三个内置 Skill：`builtin/direct_answer`、`builtin/clarify` 和 `builtin/orchestrator_plan`，各自执行逻辑不合并。
- 普通单轮、无需工具的问题由 `direct_answer` 一次回答；关键输入不足由 `clarify` 提问；复杂、多意图、需要工具或多步执行的请求由 `orchestrator_plan` 规划执行。
- 唯一高置信 Skill 直接执行：存在 `skill.yaml` 时进入 YAML SkillEngine；只有 `SKILL.md` 时进入 DeepAgents。执行成功后结束本次路径，不先调用 Plan。
- 若路由无法通过唯一高置信门槛，或有多个置信度大于 `0.5` 的 YAML 业务 Skill，则最多将前五个候选交给 Plan；低于该阈值的候选不进入候选集合。
- YAML Skill 的准入/结果评估要求转 Plan、Skill 执行失败、DeepAgents 执行异常或 Markdown 结果评估不通过，以及路由选择与候选评分不一致时，转交 Plan 并携带稳定失败原因。取消仍按取消协议处理，不伪装成普通失败。
- Plan 先根据候选 Skill 能力分解目标并匹配可执行 Skill；未被 Skill 覆盖的目标再拆成目标级工具任务。Planner 只看到工具检索能力，不接收完整工具目录；每个工具任务按任务目标从当前授权目录检索默认最多五个相关工具（由 Harness `plan_tool_candidate_limit` 配置，范围 1–20），并连同任务一起交给受限 ReAct 子 Agent。子 Agent 可在一个任务内多轮推理、重复调用同一工具或选择其他已检索工具；不要求一个任务只调用一次工具。工具子 Agent 默认最多运行五轮（由 Harness `plan_tool_agent_max_turns` 配置，范围 1–32）。每次调用仍经过 Runtime、Policy、共享预算、审批和 Tool Executor 治理链，且不能调用检索集合之外的工具。`business_skill_task` 不经过该工具 ReAct 循环，而是由 Task Executor 选择授权 Skill 后调用 SkillEngine。
- 若前一次执行结果不确定，Plan 必须先用只读能力检查当前状态，再决定是否重做动作；失败原因和不确定状态随计划输入传递。
- Router 只选择执行入口和候选，不执行 Skill，也不授予工具权限。SkillEngine 根据 ID 查找 YAML 契约；DeepAgents 根据选中的 Markdown 包执行；YAML DAG 按 `depends_on` 执行。
- YAML DAG 的未声明依赖继续按前一步依赖兼容；空依赖列表表示根步骤；就绪步骤可并行，汇聚步骤等待全部依赖完成。子 Skill 和 MCP 调用仍须通过 Runtime 校验和 HostPilot 治理链。

## 影响

- 普通问答保留轻量单轮路径，不承担 Planner、Orchestrator 或工具循环的开销；ReAct 循环只在 Plan 已分配的工具子任务内部运行，不作为独立的顶层路由。
- 唯一高置信的 Markdown Skill 由 DeepAgents 执行；这不是额外的通用 ReAct 路由。多个中置信业务 Skill 或执行/评估失败时使用 Plan。
- Plan 不向 Planner 暴露全量工具目录；具体工具按未覆盖的子任务检索，并继续经过既有治理链。
- SkillEngine 在执行前校验输入必填字段和 JSON Schema，在执行后校验输出 Schema；缺少必填字段返回澄清，Schema 类型或结构错误返回失败。高影响工具只有在 Policy 允许后才请求用户确认；Skill 的 `side_effect_level` 用于风险、幂等和补偿治理，不构成 Skill 级通用确认。

## 复核条件

若新增独立于 Plan 子任务的通用自主工具循环需求，先评估其与 Plan、预算和审批治理的关系；不得让工具调用绕开治理链。
