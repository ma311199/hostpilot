# Harness、Workspace 与 Skill 执行偏好 Store Port 记录

## 实现

- 新增 `HarnessConfigurationStore`、`WorkspaceConfigurationStore` 和 `SkillExecutionStore` Protocol。
- `create_app` 支持分别注入三个工厂；默认仍是现有 SQLite Store 和表结构。
- 保持每类接口的数据模型与约束明确，不抽象成通用字典 Store。
- 架构总览、模块职责、开发指南和 ADR-0023 已更新。

## 验证

- 定向测试：API App、Harness、Workspace 与 Skill 执行模式 Store 测试：61 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：331 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 88 个后端源文件通过。

## 后续

- Audit、Cancellation、Trace Store 仍为 SQLite 实现，分别作为安全审计、取消记录和观测数据域评估替换需求。
