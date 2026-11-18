# Audit、Cancellation 与 Trace Store Port 记录

## 实现

- 新增三个独立 Store Protocol/Factory，并通过 `create_app` 注入；SQLite 保持默认实现。
- Audit、Trace 保持同步 Sink 写入接口，Cancellation 保持异步 Snapshot 写入。
- 更新新架构文档、模块职责边界、开发指南和 ADR-0024。
- 沿用已有审计字段、Trace 属性、取消摘要的脱敏及白名单逻辑，不改变数据库结构。

## 验证

- 定向测试：API App、Audit、Cancellation、Trace：61 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：331 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 88 个后端源文件通过；`git diff --check` 通过。

## 限制

- 尚未实现第二种 Store 后端；替代实现须自行维持相同的数据最小化、白名单、查询上限和排序行为。
- 测试未采集覆盖率。
