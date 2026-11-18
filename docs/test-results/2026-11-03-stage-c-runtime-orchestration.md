# Stage C Skill Runtime 与 Plan Orchestrator 验证

> 本报告记录默认 Chat API 集成前的 Runtime 核心阶段快照。后续顶层选择器、MCP action、Plan 模型适配和 Chat API 集成状态以[Stage C 集成验证报告](2026-11-03-stage-c-integration.md)为准。

- 日期：2026-11-03
- 范围：内置 Skill 执行、业务 YAML steps、准入/结果评估边界、副作用快照及有限 Plan 调度
- 相关文档：[架构决策 ADR-0009](../decisions/0009-skill-runtime-and-yaml-contract.md)、[业务 Skill 快速路径 ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)

## 实现结果

- `YamlSkillRegistry` 解析和校验 YAML 契约；`SkillRuntime` 按 builtin Skill ID 分发，执行无工具循环的单轮 `direct_answer`、`clarify`，并将业务 Skill steps 的依赖编译成 LangGraph DAG，支持并行分支和多依赖汇聚。旧格式中省略 `depends_on` 会依赖前一步；显式空依赖表示可并行根节点。注册阶段拒绝缺失依赖、环和输入映射读取非祖先步骤。未安装可选 LangGraph 依赖时，顺序 workflow adapter 仍遵循 DAG 依赖。
- Runtime 校验调用输入和输出 JSON Schema，提供准入及结果评估端口，记录 Skill/manifest、步骤、工具调用、预算、幂等键和副作用快照。
- 升级 Plan 时检查剩余预算；不可逆副作用转人工处理，可逆副作用要求补偿句柄；评估器异常按 fail-closed 返回人工复核状态。
- `OrchestratorPlanExecutor` 提供 Plan DAG 校验与拓扑排序、串行任务执行、只读 Blackboard、验证端口、有限重规划、重复失败任务拦截、已完成等价任务复用和稳定幂等键。
- 计划失败结果保留已完成任务结果、失败任务状态、错误码、调用工具 ID 和副作用信息。
- `SkillRuntimeAgentAdapter` 实现 Chat `AgentRuntime` 兼容边界：按注入的 selector 结果调用 Skill Runtime，持久化选中 Skill 的用户/助手轮次，并把请求级预算和审批 Provider 传给 Skill action handler。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量单元/集成测试 | 167 passed |
| Ruff lint | 通过 |
| Ruff 格式检查 | 83 个文件通过 |
| mypy | 55 个源码文件通过 |
| `uv lock --check` | 通过，172 个依赖已解析 |
| `git diff --check` | 通过 |
| 前端测试 | 14 passed，5 个测试文件通过 |
| 前端 lint / production build | 通过，build 包含 Vue TypeScript 类型检查 |

测试命令为 `.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`uv lock --check`、`git diff --check`、`pnpm test`、`pnpm lint` 和 `pnpm build`。未采集覆盖率。pytest 输出一条 FastMCP 依赖的 Authlib 弃用警告，不影响结果。

## 当前边界

- Chat adapter 通过可注入的 `SkillSelectionPort` 供 HostPilot 装配；默认 Chat API 尚未提供顶层分类选择器，因此现有默认会话仍使用原 DeepAgents 路径。
- Planner、Plan Validator、Verifier、Tool/子 Skill Task Executor 仍需真实适配器；当前 Orchestrator 验证的是核心控制流及其端口行为。
- 尚未完成完整子 Skill 委派、工具 Facade 集成、取消后的补偿/回滚协议和端到端聊天验证。因此 Stage C 仍为进行中。
