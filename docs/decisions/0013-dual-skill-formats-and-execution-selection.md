# ADR-0013：并行支持 Markdown 与 YAML Skill

- 状态：已采纳
- 日期：2026-11-05
- 覆盖：[ADR-0011](0011-yaml-skill-registry-and-legacy-migration.md) 中关于旧 Markdown Skill 仅作迁移兼容、最终由 YAML 替代的决定
- 关联：[ADR-0009](0009-skill-runtime-and-yaml-contract.md)、[Skill 开发指南](../skills-guide.md)、[路线图](../roadmap.md)

## 背景

HostPilot 已有 DeepAgents `SKILL.md` 执行能力，也已实现基于 `skill.yaml` 的声明式注册、路由与 Skill Runtime。两种格式各有用途：Markdown 易于编写、适合指令驱动的 Skill；YAML 提供可校验的机器契约和 DAG 工作流。项目不应把其中一种设为另一种的迁移目标。

## 决定

- 长期并行支持两种 Skill 执行形式：DeepAgents 执行 `SKILL.md`，Skill Runtime 执行 `skill.yaml`。它们是两种正式执行入口，不是新旧格式关系。
- 默认按包内容发现执行方式：存在 `skill.yaml` 时选择 YAML Skill Runtime；只有 `SKILL.md` 时选择 DeepAgents Runtime。YAML 文件存在但契约无效时按 YAML 路径报错并禁用该包，不得回退执行 Markdown。两者都没有则不是可执行 Skill。
- 包同时含有两个文件时，默认选择 YAML Runtime；`SKILL.md` 可同时作为展示文档。不得把 Markdown 指令自动转换或解释成 YAML steps。
- Skill 配置允许用户明确选择 `yaml`、`deepagents` 或 `both`，并持久化到 SQLite；未显式配置时遵循基于文件存在性的默认规则。
- `both` 表示同一 Skill 可由两种执行入口分别承载，不表示对一次请求先后执行两次。路由应确定唯一的 Skill 执行入口；执行失败后不得自动切换到另一入口重试，以避免重复副作用。
- 两种执行入口共享 HostPilot 的预算、策略、审批、工具治理、错误处理和审计边界。格式选择不构成工具授权。
- 注册、路由与执行必须保留来源格式和执行入口信息，便于 UI 展示和 Trace 观测；YAML 结构化元数据只约束 YAML 路由，DeepAgents Markdown Skill 沿用其已校验的 frontmatter 和 SkillsMiddleware 能力。

## 影响

- ADR-0011 关于独立注册表、严格 YAML schema、拒绝自动转换等决定继续有效；其中关于旧 Markdown Skill 仅过渡兼容、最终停用的表述被本 ADR 覆盖。
- 统一 Skill Catalog 合并用户包与只读 bundled 包；目录项显示检测格式、有效入口、当前选择和启用状态。无效 YAML 阻断 Markdown 回退。
- Web 管理页面支持兼容入口选择，更新前提示切换影响；设置在 SQLite 持久化，并在运行时锁内即时更新 YAML 与 DeepAgents 过滤器。
- 分类器只接收当前启用且允许的入口；DeepAgents 选择只注入被选中的 Markdown Skill。`both` 每次请求只允许选中一个入口，执行失败不跨入口重试。
- 定向测试覆盖 Markdown-only、YAML-only、双格式默认 YAML、入口过滤、显式 DeepAgents 选择及偏好持久化；完整测试结果见 [Skill 执行方式管理验证](../test-results/2026-11-07-skill-execution-selection.md)。

## 复核条件

只有当某种格式被明确弃用，或安全与维护证据证明两种入口不能共同满足 HostPilot 治理契约时，才重新评估双格式决定。
