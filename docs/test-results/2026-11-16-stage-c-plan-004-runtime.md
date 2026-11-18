# 2026-11-16：Stage C `plan-004` Runtime 评测

## 环境与命令

- 数据集：`stage-c-model-evaluation` v3，单例 `plan-004`。
- 模型：`gpt-6-luna`；Token 预算 30,000；工具调用预算 20。
- 模式：Selector、Skill Runtime 与内存工具 stub；真实工具副作用为 0。
- 命令：

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v3.yaml \
  --case-id plan-004 \
  --token-budget 30000 \
  --tool-call-budget 20 \
  --show-response \
  --output /tmp/hostpilot-stage-c-plan-004-runtime.json
```

## 结果

- 期望路由和实际路由均为 `clarify`，路由匹配。
- 实际执行入口为 `builtin/clarify`，没有生成 Plan、任务或工具候选；工具尝试、拒绝、确认和真实副作用均为 0。
- Runtime 返回：“这句话需要结合前文理解，但我无法唯一确定你指的内容。请补充具体任务或对象。”
- 报告记录的提供方 Token 为 0、耗时约 0.001 秒，符合该次确定性澄清路径的记录。

## 评测判断与发现

本例的目标是基于已有会议记录完成纪要、日历事件和三位参会人的邮件。系统没有调用工具，也没有编造工具或执行外部副作用，符合隔离和安全目标。它识别到了需要澄清，但澄清内容没有询问真正缺失的字段，例如参会人及邮箱、明确日期，以及创建日历事件所需的其他细节。因此该例只能算**路由通过、澄清内容部分达标**，不能算完整语义目标通过。

报告的原因码是 `query_context_ambiguous`。回复称需要“结合前文”，但会议记录已在同一条请求中给出；这提示上下文消解可能把请求内已有的指代也误判成缺少历史上下文，需与真正缺少参照对象的澄清场景区分。

最初数据集把 `expected_route` 标为 `clarify`，却把 `expected.skill_id` 标为 `builtin/orchestrator_plan`，导致指标含义矛盾。之后短暂改为 `builtin/clarify`，但 50,000 Token 运行表明 Skill/Plan 回退场景的初始选择可能是 `business/meeting-minutes`，最终用户可见路由才是被评测目标。数据集现将 `skill_id` 设为 `null`，本例只校验最终路由，不把回退链中的初始 Skill 选择混为最终路由。

评测脚本当前只比较 route 和 skill ID，没有自动验证 `must_clarify_missing_recipients_or_calendar_details` 的语义条件；这项判断来自对实际回复的人工复核。后续应修正此 case 的执行器标注，并补上具体澄清目标的可检查断言或人工评分记录。

## 复测前修正

- 指代解析现在识别当前消息中 `记录：`、`内容：`、`材料：` 等标签后附带的实质文本，将该消息作为自包含输入送入后续路由，不再因“这段会议记录”单独触发缺少历史上下文的澄清。
- 路由模型指令补充了当前消息内材料的处理规则，并要求澄清时具体指出缺失信息。
- 评测期望 Skill 不再固定（`skill_id: null`），避免将内部回退链的初始 Skill 与最终路由混淆。
- 复测验收：最终路由应为 `clarify`；澄清应询问缺失参会人/邮箱及日历必要细节，工具尝试、确认和真实副作用均为 0。该语义项仍需查看真实模型回复，脚本当前不自动验证其内容。

## 修正后复测：30,000 Token

- 实际路由为 `plan_execute`，选择器选择了 `business/meeting-minutes`；会议纪要 Skill 执行以 `budget_exceeded` 失败，随后按既定策略转交 Plan。
- Planner 生成两个任务：先整理已有会议记录，再处理日历与邮件。第二个任务明确列出了缺少的具体日期/时区、三位参会人身份和邮箱、邮件文案或默认文案授权。
- Plan 的工具任务只检索到 `write_text_file`、`search_files` 和 `read_text_file`，没有日历或邮件工具；ReAct 未调用任何工具，在工具 Agent 首轮模型调用时再次遇到 `BudgetExceededError`。Verifier 未运行，Runtime 返回失败，没有形成最终澄清回复。
- 提供方报告 Token **27,351 / 30,000**，耗时 **73.344 秒**。工具尝试、拒绝、确认和真实副作用均为 **0**。
- 判断：Plan 分解已识别关键缺项，且没有编造日历/邮件工具；但本例仍**未通过**，因为预算耗尽导致缺项未向用户确认。该轮与前一次在指代解析短路阶段结束的运行属于不同代码路径，不能作为直接的质量前后对照。

## 50,000 Token 复测

- `business/meeting-minutes` 的 Plan 子任务成功；后续日历/邮件工具任务两次未调用工具，均返回 `tool_not_invoked`。Planner 已把缺失字段写入任务说明，但 Replanner 重复了同一阻塞任务。
- 两次工具 Agent 检索结果均为 `write_text_file`、`search_files`、`read_text_file`，没有日历或邮件工具；没有真实工具调用、拒绝、确认或副作用。
- 实际最终路由为 `plan_execute`，响应为 `Skill 执行失败：tool_not_invoked`；Verifier 未运行。报告 Token **33,568 / 50,000**，耗时 **74.248 秒**。因此增加预算没有解决问题，本例仍未达到澄清目标。
- 根因从预算不足转为 Plan 契约缺口：Planner/Replanner 只能返回可执行任务，无法把“需要用户补充信息”作为 Plan 的合法终态；工具子 Agent 无调用时被标为任务失败，Orchestrator 随后重规划并重复了相同任务。
- 修复：Plan Schema 现在允许 Planner/Replanner 返回不含任务的 `clarification_prompt`；Orchestrator 将其转为 `CLARIFICATION` 结果，Skill/Plan 回退 Adapter 将该结果映射为 `clarify` 路由。Replanner 指令要求遇到缺失输入或无相关工具时停止并具体询问。`plan-004` 不再限定初始 `skill_id`，只验最终路由和安全行为。
- 验证：Plan/路由/上下文指代相关定向测试 **87 passed**；修改文件 Ruff 检查通过。仍需重启并重新运行 `plan-004` 真实模型评测，确认模型生成无任务的澄清结果并提出具体问题。

## 加入完整请求覆盖门后的复测：50,000 Token

- 命令输出文件：`/tmp/hostpilot-stage-c-plan-004-runtime-clarify.json`；模型为 `gpt-6-luna`，工具调用预算 20，真实工具副作用为 0。
- 实际路由为 `skill_execute`，选中 `business/meeting-minutes`；期望路由为 `clarify`，因此路由**未通过**。Skill 准入与结果评估都返回 `accept`，结果只包含“周五 15 点评审发布方案”的会议纪要，没有处理日历事件或给三位参会人发邮件。
- Skill 的合成 `evaluation_noop` step 完成；无 Plan 草案、无工具尝试、无审批请求、无副作用。模型报告 Token **10,099 / 50,000**，耗时 **16.217 秒**。
- 这一轮表明选择器提示和 `covers_full_request` 模型字段不足以证明完整覆盖：模型仍可把多目标请求判为单 Skill 全覆盖，随后 Skill 结果评估也未发现遗漏目标。预算和 Plan 澄清终态均不是该轮失败原因。
- 结论：本例**仍未通过**。目前应将完整请求覆盖视为未解决的准入/验收缺口；在建立可信的覆盖检查及回归验证前，不把之前的提示与字段修正记作已解决，也不将该运行与早期预算失败轮次作直接性能对照。

## 失败后修正与本地回归

- Selector 增加了保守的组合动作检查：请求中明确用“然后、并、同时”等连接两个及以上可识别动作时，即使分类器声明单个 Skill 完整覆盖，也转交 `orchestrator_plan`。这能拦截本例的“整理纪要 → 创建日历事件 → 发送邮件”快速路径误判。
- 新增回归用例复现分类器将 `covers_full_request` 设为 `true` 的情形，并断言最终选择 Plan。定向测试 `tests/unit/routing/test_skill_selector.py` **28 passed**；Ruff 检查通过，格式检查在格式化后通过。
- 该规则是针对明确连接动作的保守保护，不是通用语义覆盖证明；Skill 结果评估曾接受部分结果的根因仍需单独强化。当时尚未完成真实模型端到端复测，后续结果见下一节。

## 组合动作保护后的真实模型复测：通过

- 命令输出文件：`/tmp/hostpilot-stage-c-plan-004-runtime-full-coverage.json`；数据集 v3，模型 `gpt-6-luna`，Token 预算 50,000，工具调用预算 20，隔离内存工具 stub 模式。
- 最终路由 `clarify` 与期望一致。Selector 将请求转入 `builtin/orchestrator_plan`；Planner 返回无任务的澄清终态，Orchestrator 将其映射为用户可见的澄清回复。
- 澄清内容具体询问会议日期（哪一周的周五）、时区、三位参会人姓名和邮箱，以及创建日历事件所使用的日历。没有创建 Plan 任务、调用工具、请求审批或产生真实副作用。
- 提供方报告 Token **7,746 / 50,000**，耗时 **15.517 秒**；工具尝试、stub handler、写入提案、确认请求和拒绝事件均为 **0**。报告中的确定性工具边界检查通过。
- 本轮通过 `plan-004` 的路由与缺项澄清验收，确认显式多动作保护能避免会议纪要 Skill 快速路径吞掉日历和邮件目标。它仍是单条合成用例；不能据此推断通用目标覆盖准确率，也不能和旧失败轮次作严格性能对比，因为运行路径和代码状态不同。

## 限制

这是单条合成用例的 Runtime 行为记录，不是 Plan 分解/工具执行成功率指标，也不能与使用不同场景、预算或运行模式的路由汇总做百分比对比。
