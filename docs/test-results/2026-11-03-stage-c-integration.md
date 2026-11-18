# Stage C 默认装配与治理链集成验证

> 本报告记录语义评估和 Plan 并行调度完成前的集成快照；当前进度以[语义评估报告](2026-11-03-stage-c-semantic-assessment.md)和[Plan 并行调度报告](2026-11-03-stage-c-plan-parallelism.md)为准。

- 日期：2026-11-03
- 范围：默认 Chat Runtime 装配、顶层 Skill 选择、MCP YAML action、Plan 模型适配器和聊天历史持久化
- 架构依据：[新架构文档](../新架构文档.md)、[ADR-0009](../decisions/0009-skill-runtime-and-yaml-contract.md)、[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)

## 完成内容

- 默认模型 Runtime 组装结构化 Skill Selector、三个 builtin Skill、业务 YAML Runtime 和 Plan Executor；Chat API 创建及重配模型时注入当前 MCP Provider。
- Selector 只返回已注册且启用的业务 Skill，按置信阈值和 JSON Schema 校验输入；缺参会澄清，业务 Skill 低置信时交给 Plan。
- `LLMPlanner` 获得注册工具及启用业务 Skill 的 ID 目录。`RuntimePlanValidator` 在执行前再次校验工具和 Skill ID。
- YAML MCP action 与 Plan 工具任务经 Provider 执行，透传请求预算和审批 Provider。未知工具/Skill 不进入实际工具调用。
- Chat API 集成测试验证所选业务 Skill 的执行结果、`skill_execute` 路由和用户/助手历史记录。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 177 passed |
| Ruff lint | 通过 |
| Ruff 格式检查 | 90 个文件通过 |
| mypy | 59 个源码文件通过 |
| `uv lock --check` | 通过，172 个依赖已解析 |
| `git diff --check` | 通过 |
| 前端测试 | 14 passed，5 个测试文件通过 |
| 前端 lint / production build | 通过，build 包含 Vue TypeScript 类型检查 |
| 覆盖率 | 未采集 |

执行命令：`.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`uv lock --check`、`git diff --check`、`pnpm test`、`pnpm lint`、`pnpm build`。前端命令从 `frontend/` 目录执行。

## 未覆盖的边界

- 当前测试以 fake 模型和 provider 为主，未访问真实 LLM/MCP 服务；Chat API 端到端覆盖路由、运行时边界及历史持久化，但不覆盖真实模型分类质量。
- `SchemaSkillAssessor` 只在路由和 JSON Schema 门通过后接受结果，不评估业务语义正确性；需后续实现有预算边界的过程/结果评估器。
- Plan DAG 目前按拓扑顺序串行执行。YAML steps DAG 已支持并行分支；不能把这个能力外推为 Plan task 也并行。
- MCP descriptor 没有细粒度副作用声明；除检索和 `read_text_file` 外，action 暂按不可逆处理。高影响工具仍由治理 Provider 执行策略和审批。
- 取消后的补偿/回滚协议、语义评测集、覆盖率仍待建设。

FastMCP 依赖链报告一条既有 Authlib 弃用警告，不影响验证结果。
