# 2026-11-17：Stage C v4 Plan Runtime 复测

## 范围与环境

- 数据集：`stage-c-model-evaluation` v4，`plan-001` 至 `plan-003`。
- 模型：`gpt-6-luna`，streaming enabled；每例 Token 预算 50,000、工具调用预算 20。
- 执行模式：隔离 Selector / Plan Runtime；文件读取和搜索来自合成 fixture，写入仅记录提案。
- 原始报告：`/tmp/hostpilot-stage-c-v4-plan-001-003-rerun.json`；终端输出由用户提供。
- 三例路由均命中预期（3/3）。Runtime 完成 2/3；总 Token 93,314，耗时 P50/P95 为 52.934/77.316 秒（P95 最近秩，等于最大值）。20 次工具 handler 调用、2 个写入提案、2 次确认请求；真实副作用为 0，无拒绝事件。

## 逐例结果

| 用例 | Runtime / 评估 | 工具与副作用 | Token / 延迟 | 结论 |
| --- | --- | --- | --- | --- |
| `plan-001`（首轮） | `failed`，`report_content_not_provided`；Verifier 两次拒绝，原因分别为 `report_content_unverified`、`report_content_not_provided` | 读取 README 与 ROADMAP，提交 `stage-c-status-report.md` 提案并请求确认；无实际落盘 | 33,321 / 77.316 秒 | 路由正确，任务执行器标记两个子任务成功，但最终报告正文没有获得 Verifier 接受；本轮不计为端到端通过。单例诊断复测见下节。 |
| `plan-002` | `completed`，Verifier 接受 `all_relative_markdown_links_valid_and_suggestion_proposal_recorded` | 搜索并读取 5 个 Markdown 文件；未发现失效相对链接；提交 `link-fix-suggestions.md` 提案并请求确认；无实际落盘 | 28,748 / 46.519 秒 | 路由和目标均符合预期，完成。 |
| `plan-003` | `completed`，Verifier 接受 `goal_satisfied` | 搜索和读取需求、5 个 Markdown fixture 与 3 个 Python fixture；无写入或确认 | 31,245 / 52.934 秒 | 路由和目标均符合预期，完成差距分析与修改/验证规划。 |

## `plan-001` 单例诊断复测

- 报告：`/tmp/hostpilot-stage-c-v4-plan-001-diagnostic-rerun.json`；相同模型和每例预算（50,000 Token、20 次工具调用）。
- `plan-001` 再次路由正确，Runtime `completed`，Verifier 接受 `goal_satisfied`；报告正文提案非空，目标为 `stage-c-status-report.md`，内容长度 1,008 字节。读取 README 与 ROADMAP 后提交提案并请求确认；没有实际落盘或其他副作用。
- 本次使用 29,522 Token，耗时 62.480 秒，4 次 stub handler 调用、1 个写入提案、1 次确认、0 次拒绝。
- 首轮失败保留为有效失败记录；诊断复测成功，说明失败未稳定复现。当前证据更符合模型计划/Verifier 结果不稳定，尚无依据认定存在稳定的代码缺陷，因此没有修改 Plan 执行逻辑或评测夹具。
- Plan 三例目前均至少有一次 Runtime 完成且 Verifier 接受的运行；`plan-002/003` 无需因本轮重复运行。

