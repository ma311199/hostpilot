# 2026-11-17：Stage C v4 业务 Skill Runtime 评测

## 范围与环境

- 数据集：`stage-c-model-evaluation` v4，`skill-meeting-001` 至 `skill-meeting-004`、`skill-code-001` 至 `skill-code-004`，共 8 例。
- 模型：`gpt-6-luna`，streaming enabled；每例 Token 预算 20,000、工具调用预算 20。
- 执行模式：隔离 Selector / Skill Runtime；写入与其他治理工具均为内存 stub。
- 原始报告：`/tmp/hostpilot-stage-c-v4-business-skills.json`；响应正文只在终端显示，未写入 JSON。
- 汇总：路由匹配 4/8（50%）；Skill 匹配 7/8（87.5%）；四条代码审查全部完成并被准入/结果评估接受；四条会议纪要均未完成预期的 `skill_execute`。
- Provider 报告总 Token 101,357；运行耗时 P50/P95 为 24.079/50.474 秒（P95 最近秩，等于最大值）。工具调用、写入提案、审批和真实副作用均为 0。

## 分用例结果

| 用例 | 实际路由 / Skill | Runtime 结果 | 失败原因 / 人工结论 |
| --- | --- | --- | --- |
| `skill-meeting-001` | `plan_execute` / `business/meeting-minutes` | `failed`, `budget_exceeded` | 对话回复报告 `skill_execution_AttributeError` 后回退 Plan；Plan 子任务进入 `human_review`。JSON 未含原始异常栈，具体异常位置待诊断。17,445 Token，50.474 秒。 |
| `skill-meeting-002` | `clarify` / `builtin/clarify` | `clarification` | Resolver 把本条请求中冒号后的讨论笔记当成上下文指代，0 Token 直接澄清，属确定性解析缺陷。 |
| `skill-meeting-003` | `plan_execute` / `business/meeting-minutes` | `failed`, `verifier_BudgetExceededError` | Plan 的纪要任务已执行成功，但 Verifier 因预算耗尽未完成；17,192 Token，39.555 秒。 |
| `skill-meeting-004` | `clarify` / `business/meeting-minutes` | `clarification` | 在 Skill 执行异常后，Plan 认为生成结果状态不确定并询问是否重做。无工具或副作用；预算/执行异常解决后需复测。14,438 Token，31.476 秒。 |
| `skill-code-001` | `skill_execute` / `business/code-review-check` | `completed` | 准入与结果评估接受；只基于片段给出范围受限的审查结论。13,121 Token，21.565 秒。 |
| `skill-code-002` | `skill_execute` / `business/code-review-check` | `completed` | 准入与结果评估接受；发现字符串拼接导致 SQL 注入并建议参数化查询。12,938 Token，23.491 秒。 |
| `skill-code-003` | `skill_execute` / `business/code-review-check` | `completed` | 准入与结果评估接受；指出 `x/x` 在 `x=0` 时抛出 `ZeroDivisionError`。13,085 Token，21.916 秒。 |
| `skill-code-004` | `skill_execute` / `business/code-review-check` | `completed` | 准入与结果评估接受；说明单行片段不足以支持缺陷结论，没有臆造仓库上下文。13,138 Token，24.666 秒。 |

## 修复与复测安排

- `skill-meeting-002` 修复：扩展 query resolver，使“从这段讨论里提炼……：<材料>”一类请求把冒号后的材料视为当前消息内文，不再因“这段”触发无上下文澄清。
- 查询解析回归：`.venv/bin/pytest -q tests/unit/routing/test_query_resolution.py`，**14 passed**；Ruff 检查与格式检查通过。
- `skill-meeting-001` 的异常栈没有保存在原始报告中。评测脚本现在会记录 Runtime 异常类型与末尾调用帧，供下一次隔离复测定位；不会记录异常消息或请求正文。
- `skill-meeting-001/003` 20,000 Token 预算下分别在 Plan fallback / Verifier 阶段耗尽；下一轮会议纪要复测预算提高至 30,000 Token。此结果与原轮预算不同，不作严格性能对比。
- 原始 8 例结果保留。修复后应重跑四条 meeting 用例，确认 resolver、异常栈、执行完成、最终评估和错误路径；若仍有异常，按捕获帧修复后再复测。

## 提高预算后的会议用例复测与根因

- 复测报告：`/tmp/hostpilot-stage-c-v4-meeting-skills-rerun.json`，每例预算提高到 30,000 Token。
- 结果：Skill 匹配 4/4，路由匹配 0/4，会议 Runtime 完成 0/4。Provider 报告 102,178 Token；耗时 P50/P95 为 61.603/69.359 秒。共 3 次只读内存 stub 读取，其余工具调用、写入提案、审批和真实副作用均为 0。
- 所有四例均捕获同一异常帧：`SkillRuntime._fallback_or_review` 调用 `self.plan_fallback.execute_fallback(...)` 时收到 `AttributeError`。根因是评测脚本将 `OrchestratorPlanExecutor` 直接赋给 `plan_fallback`；它只实现 `execute`，而接口要求 `execute_fallback`。这是评测 harness 接线错误，不是 meeting Skill 包或生产 OrchestratorPlanExecutor 的接口缺陷。
- `skill-meeting-001` 的失败响应还追问了输入 schema 未要求的日期/会议名称；`skill-meeting-002` 虽已通过 resolver 修复并命中 meeting Skill，随后仍被上述回退接线错误阻断。`skill-meeting-003/004` 的 Plan 回退也受同一错误影响。
- 修复 harness：评测脚本改为使用现有 `ModelPlanFallback(plan_executor)` 适配器。
- 修复 fixture：会议纪要 fixture 之前使用只回显原文的 `evaluation.noop`，没有真正完成 `pure_text_transform`；现改用预算受控的 `llm.complete`，输出 schema 为 `text`。v4 fixture 同时说明日期/会议名称可缺省，且提示未提供时标注“未说明”，不应追问。
- 回归验证：Fixture 与 ModelPlanFallback 定向测试 **26 passed**；query resolver 定向测试 **14 passed**；Ruff 检查、格式检查、评测脚本 `--help` 和 v4 YAML 解析通过。
- 以上修复尚未做真实模型复测。前两轮失败数据保留，不计为修复后通过。

## 修复后的会议 Runtime 复测

- 报告：`/tmp/hostpilot-stage-c-v4-meeting-skills-rerun2.json`；每例 Token 预算 40,000、工具调用预算 20。
- `skill-meeting-001` 至 `skill-meeting-004` 路由和 Skill 匹配均为 **4/4**；Runtime 状态 `completed`，准入和结果评估均为 `accept`，无 Runtime 异常。
- Provider 报告 52,885 Token；耗时 P50/P95 为 27.043/38.908 秒。工具调用、handler、写入提案、审批和真实副作用均为 0。
- 四份纪要均覆盖提供的讨论、决策、行动项、负责人和期限。日期/会议名称未提供时标为“未说明”，没有要求补充。
- 人工查看终端输出发现 Runtime 把 `{"text": ...}` 当作原样正文显示。根因是统一响应渲染器没有把输出字段 `text` 纳入正文字段。已修复渲染器并新增回归；Runtime Adapter / fixture 定向测试 **13 passed**，Ruff 检查和格式检查通过。
- 最近的会议运行发生在渲染器修复之前。按抽样复测范围，选 `skill-meeting-001`（会议纪要文本变换）和 `skill-code-002`（代码审查）各一例；其余 6 例不为此修复重复运行。

## 正文渲染修复抽样复测

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v4.yaml \
  --case-id skill-meeting-001 \
  --case-id skill-code-002 \
  --token-budget 40000 \
  --tool-call-budget 20 \
  --show-response \
  --output /tmp/hostpilot-stage-c-v4-response-render-rerun.json
```

## 正文渲染抽样复测结果

- 报告：`/tmp/hostpilot-stage-c-v4-response-render-rerun.json`；模型 `gpt-6-luna`，每例 Token 预算 40,000、工具调用预算 20。
- 两例均路由和 Skill 匹配正确（2/2），Runtime `completed`，准入与结果评估均为 `accept`，无异常。
- `skill-meeting-001`：13,161 Token，24.152 秒；`skill-code-002`：12,925 Token，29.183 秒。合计 26,086 Token，延迟 P50/P95 为 26.668/29.183 秒（P95 最近秩）。
- 两条终端正文均直接显示文本，不再出现 `{"text": ...}` 包装。工具调用、stub handler、写入提案、审批和真实副作用均为 0。
- 本次是针对渲染器修复的两例抽样，不代表其余业务 Skill 正文均已人工复核。
