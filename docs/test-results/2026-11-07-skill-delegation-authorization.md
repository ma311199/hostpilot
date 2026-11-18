# Stage F：子 Skill 委派授权验证

## 决定与实现

业务 Skill manifest 的 `dependencies.skills` 同时要求依赖包通过注册表预检，并作为该 Skill 转入 Plan fallback 时的子 Skill allowlist。列表为空时，不可委派其他业务 Skill。顶层内置 `orchestrator_plan` 作为系统入口，可从当前已启用且通过验证的 Skill 中选择；从业务 Skill fallback 的 Plan 只能访问父 Skill 显式声明的依赖。

Planner 提示只列出当前请求获准的 Skill，Plan Validator 使用同一份已启用目录和依赖关系重新校验。未知或禁用 Skill 返回不可用；已注册但未被父 Skill 声明的目标返回 `business_skill_not_authorized`，不会启动子 Skill。工具授权仍独立由 Tool Executor 与 Policy Engine 决定。

## 验证结果

- 测试覆盖顶层 Planner 的已启用目录、父 Skill 只看到显式依赖、未声明子 Skill 在执行前被拒绝、已声明子 Skill 获准及未知目标被拒绝。
- Registry / Runtime / Plan 定向测试：38 项通过；Planner 组装后的 allowlist 动态刷新用例也通过。
- 后端全量测试：285 项通过，1 条第三方 Authlib 弃用警告。
- `model_plan.py` 定向覆盖率：9 项测试通过，行覆盖率 75%；授权提示、动态注册表刷新、授权/拒绝与执行前校验分支均已覆盖。其余未覆盖行集中在该模块的模型调用错误映射、Plan Tool/Skill 具体执行适配和辅助序列化路径。
- Ruff 检查和格式检查通过。
- 前端全量测试：13 个文件、51 项通过；ESLint、TypeScript 类型检查和生产构建通过。
