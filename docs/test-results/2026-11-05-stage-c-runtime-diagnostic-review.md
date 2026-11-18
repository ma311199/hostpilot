# Stage C Runtime 定向评测诊断复核

- 日期：2026-11-05
- 输入报告：`/tmp/hostpilot-stage-c-runtime-diagnostics.json`
- 真实模型请求：本复核没有新增模型调用。

## 发现与修正

### 会议纪要 Skill

选择器正确返回 `business/meeting-minutes`，准入评估也返回 `accept`，且 `evaluation_noop` step 已完成。结果评估要求澄清的原因是输出称“没有提供内容”。检查评测夹具后发现，该 step 没有 `input_mapping`；Runtime 按契约只把显式映射的字段交给 action，所以 stub 确实没有收到笔记。

已修复合成 manifest 生成逻辑：把 Skill 的必填输入通过 `$ref: input.<field>` 映射给评测 action。此前的澄清结果是评测夹具错误，不应记为真实 Skill 质量缺陷。

### Plan 工具任务

模型连续两次生成了 `tool_task`，但任务 `input` 中都没有 `tool_id`。Validator 因此返回 `tool_not_registered`，拒绝执行；没有调用工具或审批 Provider。这是安全拒绝，但计划不满足读文件并写报告的目标。

Planner 提示已补充明确契约：`tool_task` 必须提供 `input.tool_id` 和 `input.arguments`；`business_skill_task` 必须提供已注册 Skill ID 和 `input.payload`；没有可用工具时不得虚构工具任务。Replanner 复用同一契约。定向单元测试也检查了这些指令。

### 越界路径与审批边界

独立的静态探针已验证：工作区外路径在参数验证时被拒绝；没有审批 Provider 的高影响写入在 handler 前被拒绝；模拟批准的写操作仅进入内存 stub。该检查不调用模型，未修改文件。

## 验证边界与下一步

定向 Planner 单元测试 **6 passed**；Ruff、格式、compileall、`git diff --check` 和零模型 Token 工具边界探针均通过。该补充没有发起任何新的真实模型请求。

截至本复核，输入映射修复和 Planner 指令修复尚未用真实模型重新运行。下一次只需运行 `skill-meeting-001` 和 `plan-001` 两个场景，以确认 Skill 结果评估能看到输入、Planner 能生成合法工具任务，并记录工具审批和 Verifier 结果。完整工具边界已由零 Token 探针覆盖。
