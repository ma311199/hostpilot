# Stage B：YAML Skill 注册表与路由验证

- 日期：2026-11-02
- 范围：`skill.yaml` schema v1、YAML Skill Registry、依赖预检、资源边界、结构化路由签名
- 决策：[ADR-0011](../decisions/0011-yaml-skill-registry-and-legacy-migration.md)
- 依赖：运行时新增 JSON Schema Draft 2020-12 校验库；开发环境新增对应 mypy 类型存根

## 已实现

- `YamlSkillRegistry` 与原 DeepAgents `SkillRegistry` 并行存在；旧格式仍供旧 Runtime 使用，不自动转换为 YAML Skill。
- 严格校验 schema version、Skill ID 命名空间、唯一 ID、examples/tags、输入输出 JSON Schema、builtin/steps 执行结构和步骤 ID。
- 校验 YAML 重复键、未知字段、路径越界、符号链接、可执行/不支持资源、单文件/总大小及文件数。
- 对声明的 Skill 和工具依赖进行预检；不满足的包不会进入有效 catalog。
- `MetadataSkillRouter` 依据 YAML examples、tags、intent_summary 做确定性保守匹配，仅返回 Skill ID；弱匹配、空请求和禁用 Skill 不产生选择。
- 新 Registry 可读取 `SKILL.md` 作为展示材料，但不会将其用于路由或执行。

## 验证结果

- 定向 Skill Registry 与 Router 测试：14 passed。
- 后端全量测试：140 passed。
- Ruff lint：通过；格式检查：76 个源文件与测试文件通过。
- mypy：51 个源文件通过。
- `uv lock --check`：通过。
- 未采集覆盖率。FastMCP/Authlib 有一条既有弃用警告，不影响测试。

## 当前边界

- YAML Skill 目前可发现、校验、做依赖预检和选路；Stage C Runtime 尚未交付，因此这些 Skill 当前不能由新 Runtime 执行。
- 初版路由是规则式元数据匹配，不提供语义相似度召回。
