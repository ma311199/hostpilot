# Stage E：Skill 启停与删除验证

- 日期：2026-11-07
- 范围：用户 Skill 包启用状态、删除 API 和 Skill 库管理操作
- 新增运行依赖：无

## 实现

- 新增 `PATCH /api/v1/skills/state`，按 Skill ID 启用或停用用户包；`DELETE /api/v1/skills?skill_id=...` 删除用户包。
- API 只通过用户 Skill 根目录下的注册表定位并校验包，内置随包 Skill 返回 404，不接受目录路径。
- YAML 包通过 `meta.enabled` 保存状态，写入采用同目录临时文件和原子替换；manifest 会规范化序列化，原始注释与排版不会保留。
- Markdown 包通过 `.hostpilot-disabled` 隐藏标记保存状态，目录仍可展示停用状态，DeepAgents 适配器不会把停用包注入 Agent。
- Skill 卡片为用户包提供启用/停用及删除操作；删除前使用浏览器确认，操作成功后刷新目录。

## 验证

- 后端全量测试：**249 passed**，覆盖 YAML/Markdown 启停、Markdown 运行时过滤、用户包删除、内置包保护和 API 往返。
- 前端 Vitest：**31 passed**，覆盖用户包状态按钮、确认删除和 API 请求。
- Ruff lint/格式检查、Mypy（73 个源文件）、ESLint、`vue-tsc` 类型检查、Vite production build 和 `git diff --check`：通过。
- 覆盖率未采集。测试期间出现锁定 FastMCP 依赖的 Authlib 弃用警告，未影响结果。

## 当前边界

- 状态操作只接受有效且可加载的用户 Skill；损坏或依赖预检失败的包不会出现在目录，也不能从此页面启停或删除。
- YAML 状态更新会重写 `skill.yaml` 的规范化内容，注释和原排版不保留。
- Runtime 选择、MCP 连接编辑/重连、Harness 凭证配置和 Trace 页面仍待实现。
