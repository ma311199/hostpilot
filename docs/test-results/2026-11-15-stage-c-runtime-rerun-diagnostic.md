# 2026-11-15：Stage C Runtime 复测诊断

## `plan-001`

首次复测路由正确，但 Plan 的工具任务均在工具候选检索前以 `TypeError` 失败。原因是 `PlanTask` 将任务输入冻结为 `mappingproxy`，而工具检索查询直接调用 `json.dumps`，导致不可变映射无法序列化。

已在 `GovernedPlanTaskExecutor._tool_candidates_for` 使用 `default=str` 兼容冻结输入，并增加回归测试。评测脚本现在保留任务级异常消息和完整 traceback，便于后续隔离模型或执行器错误。

修复后的本地验证：

- `tests/unit/skills/test_model_plan.py`：21 项通过。
- 全量后端测试（`--import-mode=importlib`）：445 项通过。
- Ruff 与 Python 编译检查通过。

尚未重新调用真实模型；应在修复后的脚本上重新执行 `plan-001`，确认读取两个文件、写入提案、审批和 Verifier 结果。

## 修复后复测结果

修复后真实模型已成功完成同一个 `tool_task`：两次 `read_text_file` 读取、一次 `write_text_file` 提案，工具 Agent 共 3 轮，确认请求 1 次，真实副作用为 0。最终失败发生在 LLM Verifier：`verifier_BudgetExceededError`。

本轮报告用量为 **18,922 / 20,000 Token**。因此该结果证明工具任务链和审批边界已通过，但不能记为 Plan 端到端完成；Verifier 没有足够剩余预算完成结果复核。下一轮应在 Harness 中把请求 Token 预算临时提高到至少 **30,000**，或降低模型输出/任务上下文后再复测，并单独记录预算变化。

隔离评测脚本现支持显式覆盖每例预算，例如：

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v3.yaml \
  --case-id plan-001 --token-budget 30000 \
  --show-response \
  --output /tmp/hostpilot-stage-c-plan-runtime-v3-30k.json
```

## route-only 单例复测

`direct-001` 已完成：路由命中 `builtin/direct_answer`，结果为 **1/1（100%）**，Embedding 与 Reranker 均成功，耗时 9.209 秒，报告 Token 4,115，工具事件为 0。该结果只证明服务恢复和单例链路正常；由于只运行了一个 direct case，不能作为完整路由质量基线，业务 Skill 覆盖指标仍为空。

随后完成 v3 route-only 评测：14 条有效用例全部请求完成，4 条未启用的会议纪要 Skill 用例跳过。路由命中 **13/14（92.86%）**，业务 Skill 精确命中 **5/5（100%）**，RRF Recall@1/3/5 为 **60%/100%/100%**，RRF MRR 为 **0.80**，业务 Gate 覆盖率和精确率均为 **100%**。分类模型共完成 11 次调用，报告 Token **45,658**；P50/P95 延迟为 **8.902/15.592 秒**，无工具事件。

唯一偏差为 `clarify-001`：被分类为 `legacy/office-automation` 的 DeepAgents Skill，评测标签期望顶层澄清。`clarify-003`、`clarify-004`、`clarify-005` 均正确进入顶层澄清。该 route-only 评测不执行 Skill，因此不能判断 `clarify-001` 后续是否会在执行层追问缺失材料。

## `clarify-001` Chat Runtime 验证

使用本机 Chat API 发送“帮我整理成会议纪要。”后，实际响应为顶层 `clarify`，提示用户提供会议转写、聊天记录或要点；请求 Token 为 **4,233**，没有工具事件。Trace 显示分类决策为 `clarify`，随后由 `builtin/clarify` YAML Skill 完成。该结果支持保留当前评测标注，不需要为 `clarify-001` 修改路由规则或标注。

## `plan-002` 首次复测诊断

首次运行以 30,000 Token、8 次工具调用预算执行。Planner 和 ReAct 子 Agent 均正常启动，但评测 stub 只有 `read_text_file` 和 `write_text_file`，没有生产工具目录中用于遍历工作区的 `search_files`；模型因此猜测文件路径并消耗完 8 次工具调用，最终返回 `tool_call_budget_exceeded`，没有提交写入提案。

该失败属于评测夹具能力与用例目标不一致，不作为 Plan 执行质量结论。隔离脚本现已加入只返回合成文件名的 `search_files` stub，并支持 `--tool-call-budget` 参数；真实文件和副作用仍不会进入评测。

修正夹具后复测成功完成两个 Plan task：`search_files` 搜索 Markdown 文件，读取 5 个文件，生成 `LINK_FIX_SUGGESTIONS.md` 写入提案，并触发 1 次确认；总工具调用 7 次，真实副作用为 0。最终仍因 `verifier_BudgetExceededError` 失败，本轮 Token 用量为 **26,128 / 30,000**。这证明任务分解、按任务工具检索、ReAct 多轮调用和审批链路均已通过；尚需提高本例预算完成 Verifier 复核。

提高至 50,000 Token 后，首轮扫描任务和写入提案仍然成功，但 Verifier 将 `stub_only` 误判为未生成报告，触发重规划；重规划任务在预算耗尽前未完成。现已补齐 `plan-002` 评测契约：用户提示和期望结果明确写入工具只记录 `proposal_recorded_not_persisted`，与 `plan-001` 保持一致。下一轮复测应验证 Verifier 接受提案级完成结果。

契约修正后的最终复测通过：扫描 **5** 个 Markdown 文件，检查 **3** 个相对链接，发现 **0** 个失效链接，提交 `markdown-link-fix-suggestions.md` 写入提案并获得 **1** 次确认；Verifier 接受，Runtime 状态为 `completed`，报告 Token **29,833**，真实副作用为 0。ReAct 首次使用了不合法的 glob 参数并收到一次 `invalid_arguments` 拒绝，随后改用合法参数重试并完成任务；该拒绝没有执行 handler，也没有导致任务失败。

## `plan-003` 首次复测诊断

首次运行中，ReAct 子 Agent 成功读取需求和 Markdown 文件，但对若干空结果 glob 反复调用 `search_files`，在 20 次工具预算内没有进入 Python 文件读取，最终以 `tool_call_budget_exceeded` 失败。评测 stub 已改为按 `.md` / `.py` 后缀兼容常见 glob，并在工具尝试记录中保留 `pattern`，便于确认搜索是否命中合成源码夹具。

第二次运行已读取 `requirements.md`、5 个 Markdown 文件和 3 个 Python 文件，但继续探索 JS/TS/Go/JSON/YAML 等不存在的文件类型，并因通配符 `*` 返回空结果而重复搜索，在 5 轮限制内失败。stub 现为 `*` 返回完整合成文件清单、`requirements*` 返回需求文件；这只改善隔离夹具反馈，不放宽生产工具的路径和 glob 校验。

夹具修正后的最终复测通过：单个 `tool_task` 搜索并读取 `requirements.md`、5 个 Markdown 文件和 3 个 Python 文件，没有写入或确认请求；Verifier 接受 `goal_fulfilled_by_analysis_and_plan`，Runtime 状态为 `completed`，Token **37,846**，真实副作用为 0。该结果证明 Plan 能在只读工具范围内完成需求—实现差距分析和修改/验证规划。

## route-only 评测

本轮在 `direct-001` 处收到 HTTP 504，报告状态为 `incomplete`，没有产生可用路由指标。API 的主模型配置正常，但当前配置的本地 Embedding（`127.0.0.1:8080`）和 Reranker（`127.0.0.1:8081`）端点均无法连接；两者不可达会使路由评测在请求时间预算内超时。该报告不作为路由质量基线。

待两个本地模型服务恢复后，再单独复跑 `direct-001`，确认请求完成后再运行完整 route-only 评测。
