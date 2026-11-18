# ADR-0011：YAML Skill 注册表与旧格式过渡

> 本 ADR 记录 Stage B 当时的迁移策略。关于长期保留两种 Skill 执行格式、默认选择和未来配置的决定已由 [ADR-0013](0013-dual-skill-formats-and-execution-selection.md) 更新。

- 状态：已采纳并实现；执行格式策略由 ADR-0013 更新
- 日期：2026-11-02
- 关联：[ADR-0009](0009-skill-runtime-and-yaml-contract.md)、[ADR-0010](0010-harness-contracts-and-configuration-boundary.md)、[Skill 开发指南](../skills-guide.md)、[路线图](../roadmap.md)

## 背景

当前实现使用 DeepAgents `SKILL.md` frontmatter 包，并会把指令交给 DeepAgents SkillsMiddleware。新架构规定 `skill.yaml` 是执行契约，`SKILL.md` 只用于展示。需要在 Runtime 切换前保留当前行为，同时避免旧格式被新路由误认为已符合机器契约。

## 决定

- 迁移期间保留现有 `SkillRegistry` 作为旧 DeepAgents Runtime 的兼容入口；旧包继续按当前行为加载，不自动转换、不推断 YAML 执行步骤。
- 新增独立 `YamlSkillRegistry`。只有含有效 `skill.yaml` 的包进入新 Skill Catalog 和新路由索引；旧 `SKILL.md` 内容可作为展示文档读取，不能驱动 YAML Runtime 或路由签名。
- 两种 Registry 使用独立的注册 API；迁移期间包可以同时被旧、新 Registry 发现，但不自动互相转换或跨 Runtime 调度。Runtime 切换时必须明确选择对应契约。
- 用户或包维护者显式迁移旧包：为其补齐 YAML 元数据、输入/输出 JSON Schema、执行定义及依赖；无需继续支持 `SKILL.md` frontmatter 自动执行适配器。
- `skill.yaml` schema v1 采用严格字段校验、JSON Schema Draft 2020-12、唯一 `skill_id` 和步骤 ID、依赖预检及资源边界检查。未知字段一律拒绝。按 ADR-0012，schema v1 还要求 `side_effect_level`。
- 用户 Skill ID 使用 `business/<id>` 或 `community/<id>`；`builtin/` 保留给 HostPilot 内置包，用户注册目录不能声明此命名空间。
- 注册表验证依赖存在性与包安全；它不执行 Skill、不授予工具权限，也不保证业务步骤语义。执行和运行时授权由 Stage C 负责。
- 初版路由仅用 YAML 的 `examples`、`tags` 与 `intent_summary` 做可复现的保守匹配；弱匹配不返回 Skill。语义模型路由可作为后续替换策略，但只能读取 YAML 路由签名。

## 影响

- 旧 Markdown Skill 的当前行为在迁移期不变；新 Router 不会把只有 `SKILL.md` 的包选为 YAML Skill。
- 新 YAML 包可以被发现、校验、做依赖预检并选出 `skill_id`，但在 Stage C Runtime 交付前不能由新 Runtime 执行。
- `skill.yaml` v1 字段及已支持的 step 描述形式见 [Skill 开发指南](../skills-guide.md)。修改既有包格式需更新 schema 版本或提供兼容迁移。

## 复核条件

不得静默地将 Markdown 指令解释成 YAML steps。关于长期保留两种执行形式及其选择规则，见 [ADR-0013](0013-dual-skill-formats-and-execution-selection.md)。
