# ADR-0009：Skill Runtime 与 YAML 执行契约

- 状态：已采纳为目标架构；实现分阶段迁移
- 日期：2026-11-02
- 后续 [ADR-0029](0029-three-skill-pipelines.md) 删除独立 ReAct 工具循环；普通无工具问答仍由 `direct_answer` 处理，三个 builtin Skill 注册及 Runtime/DAG 契约继续有效。
- 关联：[新架构文档](../新架构文档.md)、[需求基线](../requirements.md)、[路线图](../roadmap.md)、[Skill 开发指南](../skills-guide.md)、[ADR-0012](0012-business-skill-fast-path-and-plan-fallback.md)

## 背景

当前 HostPilot 通过 DeepAgents 适配器加载 `SKILL.md` 包，Router 也尚未以 Skill ID 为唯一执行入口。新架构需要把路由选择与 Skill 执行分开，使 Skill 能声明式描述，并让内置编排能力复用 HostPilot 的策略、预算、审批和观测服务。

## 决定

- Router 只从注册的业务 Skill 和内置 Skill 中选择 `skill_id`；Skill Runtime 按 ID 加载、校验并执行。
- 保留三个内置 Skill：`direct_answer`、`clarify`、`orchestrator_plan`。`direct_answer` 是不含 ReAct 工具循环的单轮 LLM 回答；高置信命中单一业务 Skill 可走经过评估的快速路径；需要规划的复杂、多意图或无业务 Skill 匹配的执行任务由 `orchestrator_plan` 接管；信息不足时使用 `clarify`。路由与副作用安全细节以 ADR-0012 为准。
- YAML 业务 Skill 的机器契约为 `skill.yaml`，声明路由元数据、输入/输出 schema 和 steps。特殊控制流通过内置 Executor 插件实现，YAML 壳通过 `skill_type` 选择插件。DeepAgents Markdown Skill 也是正式支持的执行形式；两者的选择规则见 [ADR-0013](0013-dual-skill-formats-and-execution-selection.md)。
- `YamlSkillRegistry` 负责解析和校验契约；Runtime 将 `depends_on` 编译为 LangGraph DAG。未声明 `depends_on` 的旧式 step 默认依赖前一步；显式空列表表示根节点，可与其他就绪节点并行。并行节点输出由 reducer 合并，汇聚节点等待全部依赖完成。LangGraph 通过适配器隔离，未安装可选 agents 依赖时使用遵循依赖关系的顺序执行器。
- `SKILL.md` 可由 DeepAgents 执行，也可作为 UI 展示材料；当 YAML Runtime 执行 Skill 时，它不是执行输入。两种格式的路由和默认选择规则见 ADR-0013。
- 业务 Skill 的 `dependencies.skills` 必须通过注册表预检；当该 Skill 的评估或执行失败转入 Plan 时，同一列表限定可选择的子 Skill 候选，空列表默认拒绝委派。顶层内置 `orchestrator_plan` 可在已启用且通过校验的业务 Skill 中选择；Plan 若源自某个业务 Skill，则只能选择该父 Skill 声明的依赖。Plan Validator 在任何子 Skill task 执行前重新检查授权。
- 子 Skill 声明只定义候选范围，不授予工具权限或绕过启用状态、预算与委派深度限制；MCP 工具仍须经过 HostPilot Tool Executor 与 Policy Engine。

## 影响

- DeepAgents Markdown Skill 与 YAML Skill Runtime 长期并行支持；默认根据包内文件选择执行入口，具体规则见 ADR-0013。
- `skill.yaml` 的完整 schema、条件/错误 step 语义、上下文快照与回滚格式需在 Runtime 实现前细化。
- 核心 Skill 契约和 Runtime ports 不依赖 LangGraph 专有类型；LangGraph DAG 编译器是当前 steps 执行后端，仍由适配器封装。
- 需要覆盖 Router 选择、三个内置 Skill、steps、子 Skill 循环限制、预算传播、失败回滚及不可信 Skill 声明的安全测试。

## 复核条件

当业务验证表明声明式 steps 无法安全或清晰表达所需流程，或双格式实现无法共同满足治理契约时，复核 Skill DSL 与执行后端边界。格式长期并行的决定见 ADR-0013。
