# Stage C Plan Runtime 复测

- 日期：2026-11-05
- 评测集：`stage-c-model-evaluation` v2，`plan-001`
- 输入报告：`/tmp/hostpilot-stage-c-plan-recheck.json`
- 模型：`gpt-6-luna`，streaming enabled
- 模式：真实模型选择/规划/复核 + 合成文件夹具 + 内存工具 stub
- 真实工具副作用：0

## 结果

| 检查项 | 结果 |
| --- | --- |
| 路由 | 正确选择 `builtin/orchestrator_plan`，reason 为 `multi_step_project_inspection_and_report` |
| 首轮计划 | 合法列出读取 README、读取路线图、比较状态、写报告；工具任务包含 `tool_id` |
| 首轮任务执行 | 两次读取、一次 reasoning、一次写入提案均在 stub 中成功；写入请求经过 1 次审批 |
| 结果复核 | Verifier 以 `insufficient_source_content_report_is_stub` 拒绝报告 |
| 重规划 | 生成读取任务，但 `arguments.path` 缺失；stub 参数验证拒绝，Runtime 最终 `failed`，错误为 `ToolDeniedError` |
| 边界探针 | 通过：越界写入和无审批写入均在 handler 前拒绝；批准写入只记录在内存 stub；真实文件未创建 |

模型正确识别了路由并生成了首轮可执行计划。整条任务没有完成：评测夹具只提供泛化的合成 README/路线图文本，Verifier 因此无法确认报告是否基于充分来源信息；随后 Replanner 又遗漏读取路径。首轮写入仅代表批准后的内存提案，不代表真实文件已写入。

本例报告用量为 **16,644 tokens**，耗时 **41.704 秒**。报告中 `real_tool_side_effects` 为 0。

## 结论与后续

本轮不能记为 Plan Runtime 端到端通过。路由、首轮工具 ID、审批门和 stub 执行均有正向证据；Verifier 对合成来源文本的判断和 Replanner 的参数完整性仍未通过。

复测后已完成以下修正：

- 合成 README 和路线图 fixture 现在包含可比较的功能现状与进行中/未完成事项；`plan-001` 明确给出 `README.md`、`ROADMAP.md` 路径及报告文件名。
- Planner/Replanner 共用的提示约束要求提供完整工具参数，只使用目标、上下文或已完成结果中的值，不得猜测缺失参数。
- 评测记录继续区分任务执行、Verifier 判定、stub 调用和审批，不把内存提案记作磁盘写入。

该阶段的相关验证：`tests/unit/skills/test_model_plan.py` **6 passed**；Ruff 检查与格式检查通过；评测集 YAML 解析通过；零 Token 工具边界探针通过。当时尚未再次调用真实模型，随后复测记录如下。

本复测不改变 Router、Skill Runtime、Plan DAG、Policy Engine 或 Tool Executor 的架构边界，也未触碰生产文件。

## 后续复测：目标与 stub 语义

修正后再次运行 `plan-001`，路由和所有首轮任务均成功：读取 `README.md`、`ROADMAP.md`，生成比较结果，并经 1 次审批向 `stage-c-status-report.md` 提交写入提案。没有缺失路径，也没有拒绝审计事件。

本轮使用 `gpt-6-luna`、streaming enabled，报告用量 **13,625 tokens**，耗时 **54.311 秒**。真实工具副作用为 0。

Verifier 返回 `report_write_stub_only`。此拒绝符合本轮实际工具行为：stub 只记录写入提案，无法证明报告已写入磁盘。随后模型的 Replanner 响应未能解析/校验为有效结构化计划，Runtime 以 `replanner_ValueError` 结束。评测报告没有保留底层 ValueError 消息，因此目前无法再细分是空响应、非法 JSON 还是 schema 错误。真实文件副作用仍为 0；边界探针通过。

这暴露的是评测目标和 stub 能力不匹配：用例请求实际写入，但评测禁止落盘。已将 `plan-001` 的目标调整为生成报告并提交写入提案，并在期望中明确 `proposal_recorded_not_persisted`。下一轮只需复跑该用例，验证 Verifier 接受提案级目标；这不应被表述为真实磁盘写入已完成。

## 提案级目标复测通过

调整目标后再次运行 `plan-001`，真实模型选择 `builtin/orchestrator_plan`，Planner 生成两次读取、一次比较和一次写入提案任务；所有任务成功，Verifier 返回 `accepted=true`、`goal_satisfied`，Runtime 状态为 `completed`。写入提案经过 1 次审批，stub 记录 1 项提案；没有拒绝审计事件，没有触发重规划。

模型为 `gpt-6-luna`，streaming enabled；提供方报告 **14,099 tokens**，耗时 **36.236 秒**。真实工具副作用为 0，未创建或修改磁盘文件；确定性工具边界探针通过。因此本轮证明的是隔离 harness 下“计划执行并提交写入提案”端到端通过，不代表真实文件写入已验证。
