# 2026-11-17：Stage C v4 direct 与 clarify Runtime 评测

## 范围与环境

- 数据集：`stage-c-model-evaluation` v4，`direct-001` 至 `direct-005`、`clarify-001` 至 `clarify-005`，共 10 例。
- 模型：`gpt-6-luna`，streaming enabled；每例 Token 预算 20,000、工具调用预算 20。
- 执行模式：隔离 Selector / Skill Runtime，治理工具为内存 stub。
- 原始报告：`/tmp/hostpilot-stage-c-v4-direct-clarify.json`；`--show-response` 正文只输出到终端，未写入 JSON。
- 覆盖数据集 v4；和 v1–v3 结果不作直接对比。

## 路由与执行结果

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 路由匹配 | 10/10（100%） | direct 5/5，clarify 5/5 |
| Skill 匹配 | 10/10（100%） | clarify-001 通过 Plan 回退追问会议笔记；clarify-002 选中代码审查 Skill 后在执行层追问必填输入 |
| Provider 报告 Token | 43,799 | 其中 3 条确定性澄清为 0 Token；不等同于纯模型调用 Token |
| 运行耗时 P50 / P95 | 6.193 / 12.592 秒 | 10 例运行耗时含 3 条零 Token 确定性澄清；P95 使用最近秩，等于最大值 |
| 工具调用 / handler / 写入提案 / 审批 | 0 / 0 / 0 / 0 | 10 例均未调用工具 |
| 真实副作用 | 0 | 评测使用隔离环境与内存 stub |

JSON 报告记录 10/10 用例无异常。内置确定性工具边界探针通过：越界写入被拒绝、未审批写入被拒绝、审批后的 stub 写入只记录提案而不落盘。

## 人工检查与失败原因

- `direct-001` 至 `direct-005`：回答符合各自意图；翻译准确，算术正确，LangGraph 概括恰为三句。
- `clarify-001`：明确索取会议记录或笔记，满足预期。
- `clarify-002`：路由和 Skill 均匹配，但回复把 schema 字段名 `code_or_diff` 原样展示给用户，属于不合格的用户提示。
- `clarify-003`：应询问待整理内容和期望格式，实际只泛问“具体任务或对象”。
- `clarify-004`：应分别询问“它”指什么以及要如何修改，实际仍只泛问“具体任务或对象”。
- `clarify-005`：要求补充具体任务或对象，基本满足识别分析对象缺失的要求。
- 因此，本轮路由准确率为 10/10，但澄清内容不能判为全通过；不把路由准确率当作澄清质量分数。

## 修复及复测

- 首个缺陷：代码审查 Skill 的必需输入 schema 未提供面向用户的字段标题，执行层按字段名回退，产生 `code_or_diff` 内部术语。已在应用包输入 schema 增加用户可读标题“要评审的代码或 diff”；代码审查 Skill 定向单测 **2 项通过**。
- 首次 Runtime 复测（`/tmp/hostpilot-stage-c-v4-clarify-002-rerun.json`）仍失败，未验证上述标题修复：分类器选中代码审查 Skill，但标记 `covers_full_request=false`，选择器将单一代码评审请求转为 `skill_does_not_cover_full_request` 并进入 Plan。Plan 工具代理随后耗尽预算，最终路由 `plan_execute`，与期望 `clarify` 不匹配。
- 该轮每例预算 20,000 Token / 20 次工具调用；Provider 报告 17,571 Token，运行 35.815 秒。Plan 执行 10 次只读内存 stub 调用后返回 `budget_exceeded`。报告没有暴露触发预算错误的精确子预算，故不把它解释成单纯 Token 总量超限。写入提案、审批请求和真实副作用均为 0。
- 第二个缺陷与提示文案无关：选择器把“分类器认为 Skill 覆盖不完整”优先于 schema 必填槽位检查，导致缺少代码输入时没有进入 Skill 执行层澄清。
- 修复：对明确的多动作请求继续优先 Plan；对单一业务 Skill 请求，如果其已选 Skill 存在缺失必填输入，则继续经过置信 Gate 并让执行层澄清，即使分类器将 `covers_full_request` 标为 false。补充回归测试覆盖这一分类器矛盾。
- 修复验证：选择器测试 **31 项通过**；代码审查 Skill 测试 **2 项通过**；Ruff 检查、格式检查和 `git diff --check` 通过。
- 第二次真实模型 Runtime 复测（`/tmp/hostpilot-stage-c-v4-clarify-002-rerun2.json`）通过：最终路由 `clarify`，Skill 为 `business/code-review-check`，Reason 为 `business_skill_slots_pending_execution_clarification`；面向用户的提示为“请补充以下信息后我再执行该 Skill：要评审的代码或 diff。”无 Plan、工具调用、写入提案、审批或真实副作用。Provider 报告 3,895 Token，耗时 9.948 秒。
- 对照首轮不合格文案和首次 Runtime 复测的错误 Plan 回退，修复后的单例路由与槽位澄清均符合预期。失败历史保留，不计入修复后结果。
- `clarify-003/004` 的泛化提问已另行修复：内容整理请求会索取原文和期望格式；修改请求会询问指代对象和修改目标。`clarify-005` 现在会索取待分析的问题或材料，并可选询问关注重点。
- 修复后真实 Runtime 复测（`/tmp/hostpilot-stage-c-v4-clarify-003-005-rerun.json`）三例全部通过，路由和 Skill 匹配均为 3/3；三个澄清均由确定性 query resolver 处理，合计 0 Token，耗时分别为 0.001、0、0 秒。工具调用、handler、写入提案、审批和真实副作用均为 0。
- 查询澄清定向测试：`.venv/bin/pytest -q tests/unit/routing/test_query_resolution.py`，**13 passed**；Ruff 检查与格式检查通过。
- 汇总 `clarify-001` 至 `clarify-005`：路由与 Skill 匹配均为 5/5；当前五条澄清提示均按人工标注清楚询问了缺失信息。clarify-002 与 clarify-003 至 clarify-005 的单例复测和修复轨迹见本报告。

## 下一批：业务 Skill

继续评估剩余 8 条业务 Skill 用例：

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v4.yaml \
  --case-id skill-meeting-001 --case-id skill-meeting-002 \
  --case-id skill-meeting-003 --case-id skill-meeting-004 \
  --case-id skill-code-001 --case-id skill-code-002 \
  --case-id skill-code-003 --case-id skill-code-004 \
  --token-budget 20000 \
  --tool-call-budget 20 \
  --show-response \
  --output /tmp/hostpilot-stage-c-v4-business-skills.json
```

修复后还应继续评估 `skill-meeting-001` 至 `skill-meeting-004`、`skill-code-001` 至 `skill-code-004` 及 `plan-001` 至 `plan-003`。
