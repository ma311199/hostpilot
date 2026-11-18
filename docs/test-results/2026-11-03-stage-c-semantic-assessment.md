# Stage C 业务 Skill 语义评估

> 本报告记录 Plan task 并行调度前的状态快照；当前 Plan 调度能力见[后续报告](2026-11-03-stage-c-plan-parallelism.md)。

- 日期：2026-11-03
- 范围：业务 Skill 执行前准入评估、执行后结果评估、评估 Token 预算与用量上报
- 架构依据：[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)

## 实现

- 默认配置 Runtime 使用结构化 LLM 评估器：执行前判断 Skill 是否适用；执行后根据输入、输出和 step 快照评估是否达成目标。
- 评估输出限定为 `accept`、`clarify`、`plan`、`reject`，并校验 confidence、reason code 和澄清消息字段。
- 低于配置阈值的 `accept` 转为 Plan；明确拒绝在执行 workflow 前停止，不调用任何 action。
- Skill YAML、用户内容、上下文和步骤结果都按不可信数据处理。评估上下文限制最近消息数、字符串长度及集合元素数，避免把无界结果直接送入模型。
- 评估适配器接收请求级 `RequestBudget`，消耗实际模型 Token；Usage 汇总进 Skill 响应，进而由 Chat API 记录成本。
- 评估失败继续 fail closed：准入阶段不运行 steps，执行后失败进入人工复核；最终的 Schema、Policy Engine、Tool Executor 校验仍是独立的确定性安全门。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 180 passed |
| Ruff lint / 格式检查 | 通过，90 个文件已格式化 |
| mypy | 59 个源码文件通过 |
| `uv lock --check` / `git diff --check` | 通过 |
| 前端测试 | 14 passed，5 个测试文件通过 |
| 前端 lint / production build | 通过，build 包含 Vue TypeScript 类型检查 |
| 覆盖率 | 未采集 |

执行命令：`.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`uv lock --check`、`git diff --check`、`pnpm test`、`pnpm lint`、`pnpm build`。前端命令从 `frontend/` 目录执行。

## 限制与后续

- 自动化评估使用 fake 模型，只验证协议、预算和分支行为；没有真实模型评测集，无法据此保证判定质量。
- 需要建立代表性的业务 Skill admission/result 评测样例，观察误判率、澄清质量、Plan 升级率和 Token 成本，再校准 confidence 阈值。
- Plan task 仍串行执行；取消后的补偿和回滚协议尚未实现。

FastMCP 依赖链报告一条既有 Authlib 弃用警告，不影响验证结果。
