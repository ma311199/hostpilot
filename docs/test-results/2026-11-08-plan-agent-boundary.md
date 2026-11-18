# Plan Agent职责边界优化记录

## 改动

- Planner 输出任务目标、执行类型、依赖、输入和预期产出，不再输出具体 `tool_id` / `skill_id`。
- Planner 接收精简能力目录，包含能力类型、覆盖范围、前置条件和输出描述；目录不暴露可执行 ID 或参数 Schema。
- 工具任务和业务 Skill 任务执行时，由受限的能力选择阶段从当前请求授权的候选列表中选择能力和输入，再经既有 Tool Provider / Skill Runtime 执行。
- Planner 指令增加任务粒度约束、简单/中等/复杂规模建议、合并优先原则和正反例。
- Plan Validator 根据目标复杂度限制子任务数，并拒绝重复目标及没有明确预期产出的子任务。
- 执行 ID 的合法性与授权依据仍由注册表/依赖声明控制，工具调用继续使用既有预算和审批 Provider。

## 验证

命令：

```text
.venv/bin/pytest -q tests/unit/skills/test_model_plan.py tests/unit/skills/test_orchestration.py
```

结果：28 passed。覆盖 Planner 拒绝在任务输入中嵌入能力 ID 或执行参数，以及复杂度上限、重复目标和缺失预期产出的规则检查。

全量后端测试：323 passed，1 条第三方 Authlib 弃用警告。

静态检查：本次相关代码 Ruff 通过，`model_plan.py` 与 `orchestration.py` 的 MyPy 检查通过。

## 后续

语义冗余/可合并检测、局部纠偏反馈与分解质量评测集仍需后续实现。复杂度启发式需要用真实任务集校准。
