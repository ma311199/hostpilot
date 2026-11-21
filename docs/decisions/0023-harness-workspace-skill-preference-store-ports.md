# ADR-0023：Harness、Workspace 与 Skill 偏好 Store Port

## 状态

已采纳

## 背景

Harness 请求限制、Workspace 路径和每个 Skill 的执行模式都是有独立生命周期的持久化数据域。API 目前直接构造 SQLite Store，替换或隔离测试需要应用层直接依赖具体实现。

## 决定

- 定义 `HarnessConfigurationStore`、`WorkspaceConfigurationStore`、`SkillExecutionStore` 三个独立 Protocol，并允许 `create_app` 分别注入 Factory。
- 默认继续使用原有 SQLite 实现与数据库表，不迁移数据格式。
- Harness 保存已验证的 `HarnessConfiguration`；Workspace 保存路径字符串；Skill Preference 保存 Skill ID 和受限执行模式。三类数据不通过宽泛的通用字典/JSON Store 共享接口。
- 审计、取消和 Trace Store 不纳入本次配置 Store Port，保留独立记录/观测语义。
- 当前通过应用代码依赖注入选择实现，不增加环境变量后端 ID、UI 后端选择或通用 Store Registry。

## 影响

- 运行时参数、工作区选择和 Skill 执行入口可以独立替换或使用测试替身。
- 自定义 Store 必须保留配置校验、执行模式白名单与路径检查的既有业务约束；Port 不提供额外权限或迁移保证。

## 复核条件

需要将这些数据迁移至不同数据库/进程或运行时切换后端时，复核事务、并发更新、迁移与回退方案。
