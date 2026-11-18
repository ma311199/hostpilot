# Stage E：Skill 库只读目录验证

- 日期：2026-11-07
- 范围：本地 Skill 目录 API 与 Vue 只读浏览页面
- 新增运行依赖：无

## 实现

- 新增 `GET /api/v1/skills`，聚合用户目录内的 YAML、`SKILL.md` 包和随包 YAML Skill。
- 响应只包含通过校验的 Skill ID、名称、说明、执行格式、启用状态和来源，并提供无效包数量；不返回本地路径、Skill 指令正文或资源文件。
- Vue 导航新增 Skill 库页面，支持刷新、加载/错误/空状态，并区分 YAML 与 Markdown 格式、随包与用户来源及启停状态。
- 当前交付为只读目录；导入、删除、启停、文件详情和 Runtime 选择仍未实现。

## 验证

- 后端 API 定向测试：**28 passed**；后端全量测试：**231 passed**。
- 前端 Vitest：**25 passed**。
- Ruff lint、ESLint 和 `vue-tsc` 类型检查：通过。
- Vite production build：通过。
- 覆盖率未采集；测试期间有来自锁定 FastMCP 依赖的 Authlib 弃用警告，未影响结果。

## 当前边界

- 无效目录只返回数量，不公开本地路径或校验异常文本。
- 本轮没有写操作 API；页面展示的启用状态来自 YAML manifest，旧格式 `SKILL.md` 包按现有运行时行为展示为启用。
