# ADR-0013：并行支持 Markdown 与 YAML Skill

- 状态：已采纳
- 日期：2026-11-05
- 覆盖：[ADR-0011](0011-yaml-skill-registry-and-legacy-migration.md) 中关于旧 Markdown Skill 仅作迁移兼容、最终由 YAML 替代的决定
- 关联：[ADR-0009](0009-skill-runtime-and-yaml-contract.md)、[Skill 开发指南](../skills-guide.md)、[路线图](../roadmap.md)

## 背景

HostPilot 已有 DeepAgents `SKILL.md` 执行能力，也已实现基于 `skill.yaml` 的声明式注册、路由与 SkillEngine。两种格式各有用途：Markdown 易于编写、适合指令驱动的 Skill；YAML 提供可校验的机器契约和 DAG 工作流。项目不应把其中一种设为另一种的迁移目标。

## 决定

- 长期并行支持 Markdown `SKILL.md` 和 YAML `skill.yaml` 两种 Skill 格式；格式本身不绑定某个框架。每种格式保留自己的发现、解析和校验适配器，并转换为 Runtime 可执行的规范 Skill 请求。
- 默认按包内容发现执行格式：存在 `skill.yaml` 时选择 YAML 执行器；只有 `SKILL.md` 时选择 Markdown 执行器（当前由 DeepAgents 适配器实现）。YAML 文件存在但契约无效时按 YAML 路径报错并禁用该包，不得回退执行 Markdown。两者都没有则不是可执行 Skill。
- 包同时含有两个文件时，默认选择 YAML Runtime；`SKILL.md` 可同时作为展示文档。不得把 Markdown 指令自动转换或解释成 YAML steps。
- Runtime Chat 通过统一 Skill 执行契约调度选中的规范 Skill 请求，并对不同格式应用相同的 RequestContext、预算、工具授权、审批、取消、状态、恢复和结果检查。具体执行器可由 HostPilot SkillEngine 或受治理的框架插件提供；框架不得绕开 Runtime 自行启动或重试副作用操作。
- 用户可明确选择 `yaml`、`markdown` 或 `both`，并持久化到 SQLite；`markdown` 表示 `SKILL.md` 格式，不指定其执行框架。历史值 `deepagents` 在数据库启动迁移时转换为 `markdown`，旧 API 客户端输入也会归一化为 `markdown`。未显式配置时仍按包内容选择可用格式。
- `both` 表示同一 Skill 可由两种执行入口分别承载，不表示对一次请求先后执行两次。路由应确定唯一的 Skill 执行入口；执行失败后不得自动切换到另一入口重试，以避免重复副作用。
- 两种格式共享 Runtime 的预算、策略、审批、工具治理、错误处理和审计边界。格式选择不构成工具授权；`both` 表示两个适配器都可用，不表示同一次请求执行两次。
- 注册、路由与执行必须保留来源格式和执行入口信息，便于 UI 展示和 Trace 观测；YAML 结构化元数据只约束 YAML 路由，Markdown Skill 沿用其已校验的 frontmatter 和执行器能力。

## 影响

- **实现进度（2026-11-20）**：新增共享 `SkillExecutor.execute(SkillExecutionRequest)` 契约；YAML `SkillEngine` 与 Markdown 执行器通过同一调用点执行，并共享结果处理、回退、Trace 和 Turn 记录。格式选择值已统一为 `yaml`、`markdown`、`both`；DeepAgents 仅保留为当前 Markdown 执行器实现。SQLite 旧值迁移和旧 API 输入兼容已加入。
- ADR-0011 关于独立注册表、严格 YAML schema、拒绝自动转换等决定继续有效；其中关于旧 Markdown Skill 仅过渡兼容、最终停用的表述被本 ADR 覆盖。
- 统一 Skill Catalog 合并用户包与只读 bundled 包；目录项显示检测格式、有效入口、当前选择和启用状态。无效 YAML 阻断 Markdown 回退。
- Web 管理页面支持格式入口选择，更新前提示切换影响；设置在 SQLite 持久化，并在运行时锁内即时更新 YAML 与 Markdown 过滤器。
- 分类器只接收当前启用且允许的格式入口；Markdown 选择只注入被选中的 Skill。`both` 每次请求只允许选中一个入口，执行失败不跨入口重试。
- 定向测试覆盖 Markdown-only、YAML-only、双格式默认 YAML、入口过滤、显式 Markdown 选择及偏好持久化；完整测试结果见 [Skill 执行方式管理验证](../test-results/2026-11-07-skill-execution-selection.md)。

## 复核条件

只有当某种格式被明确弃用，或安全与维护证据证明两种入口不能共同满足 HostPilot 治理契约时，才重新评估双格式决定。
