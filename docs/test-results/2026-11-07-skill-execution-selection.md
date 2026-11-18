# Skill 执行方式管理验证

## 范围

依照 [ADR-0013](../decisions/0013-dual-skill-formats-and-execution-selection.md)，实现统一发现 YAML 与 Markdown Skill、显式执行入口配置、`both` 单入口路由、设置持久化和运行时即时生效。

## 实现结果

- Skill 目录合并用户目录和只读 bundled 目录；同目录双格式包以 YAML `skill_id` 作为唯一标识。
- `SkillRegistry` 可扫描只读附加目录，bundled 代码审查 Skill 的 Markdown 文件补齐有效 frontmatter。
- 分类器只接收启用且允许的 YAML/DeepAgents 候选；DeepAgents 被选中时只向图输入注入该 Skill。
- YAML-only、Markdown-only 和双格式包的可选入口按包格式限制。双格式默认 YAML，偏好通过 SQLite 保存。
- `yaml`、`deepagents`、`both` 更新在运行时锁内写入，并共享到 YAML 注册表、DeepAgents 适配器和选择器。
- 启停双格式 Skill 时同步更新 YAML 启用标记与 Markdown 隐藏标记；删除 Skill 时清理对应执行偏好。
- 前端显示检测格式、入口配置与当前选择，变更前展示确认提示。

## 验证结果

- 后端：`pytest -q` 全量通过（273 项）。
- Ruff：`ruff check src/hostpilot tests` 和 `ruff format --check src/hostpilot tests` 通过。
- Mypy：`mypy src/hostpilot` 通过，77 个源文件无问题。
- 前端：Vitest 12 个文件、46 项测试通过；ESLint、Vue 类型检查与 Vite 生产构建通过。
- 测试期间发现并修正 bundled 双格式 Skill 未被 Markdown Registry 扫描的问题，以及过时的前端 bundled 编辑提示断言。

## 限制与后续

目前测试覆盖执行入口选择与各自过滤，但没有连接真实模型或启动外部 MCP 子进程。下一项按路线图继续补充 Trace 中的 Skill、工具和审批 Span 关联。
