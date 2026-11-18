# Stage F：YAML Skill 工具授权验证

## 范围

为 YAML Skill 增加 `dependencies.tools` allowlist，覆盖声明式 MCP action 和业务 Skill Plan fallback。顶层 `builtin/orchestrator_plan` 可使用当前注册工具；业务 Skill 的空 allowlist 默认无工具权限。实际工具调用仍由现有 Tool Executor 和 Policy Engine 处理。

## 实现与安全检查点

- 注册表预检拒绝未知工具依赖，并拒绝未在 `dependencies.tools` 声明的已注册 MCP action。
- Skill Runtime 在准入评估和执行任何 step 前核对 action 的 `tool_id`；未授权时不调用评估器或工具。
- Planner 只向业务 Skill 的 Plan fallback 提供该父 Skill 声明且当前已注册的工具。
- Runtime Plan Validator 在执行前再次核对工具注册状态和父 Skill 授权；顶层 builtin Planner 仍可使用当前注册工具。
- MCP action registration 将稳定工具 ID 标记为 `tool_id`；Tool Executor 继续负责参数校验、预算、策略及审批。

## 验证

- 定向测试：`.venv/bin/pytest -q tests/unit/skills/test_model_plan.py tests/unit/skills/test_skill_runtime.py tests/unit/skills/test_yaml_registry.py tests/unit/skills/test_mcp_actions.py`，45 项通过。
- 后端全量测试：`.venv/bin/pytest -q tests`，288 项通过，0 项失败；出现 1 条第三方 Authlib 弃用警告。
- 静态检查：`.venv/bin/ruff check src/hostpilot tests`，通过。
- 前端质量构建：`pnpm build`（含 `vue-tsc` 类型检查与 Vite 生产构建），通过。
- 修改 `test_skill_runtime.py` 一处导入顺序以满足 Ruff。
- 未测量覆盖率；未连接真实 MCP Server 或模型。

## 后续

继续强化工具参数 Schema 校验，并验证超时、取消、重试和审计。业务 Skill 的 allowlist 是 Harness 内的能力约束，不替代用户/系统策略授权。
