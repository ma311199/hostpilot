# Runtime Session 生命周期迁移验证

## 范围

本阶段将 Session 访问准入、跨 worker 操作租约、删除协调和 Host 关闭排空迁入 Runtime 生命周期管理。Session 状态与租约由可替换的 `SessionLifecycleStore` Port 管理，当前 SQLite 实现仅是该 Port 的一种适配器。Conversation、Checkpoint、Memory 的实际删除仍调用现有存储实现；Session 生命周期负责协调何时可以删除。

## 行为与边界

- 新 Session 创建时登记为 `active`；旧 Conversation 在 State Services 启动时补登记，且 `deleted` tombstone 不会被旧 Conversation 回填复活。
- Chat Turn、历史读取、route-only 评测、单 Session 元数据读取、SandboxMode 升级及删除操作在完整执行期间持有持久 Session 租约。进入 `closing` 后拒绝新租约。
- 删除关闭准入后等待所有有效 Host 的操作租约排空，再执行 Checkpoint、Memory 和 Conversation 清理；清理失败时保留 `closing`，释放删除认领，允许本 Host 或其他有效 Host 重试。
- 每个 ASGI worker 的 Runtime Host 续租到本地 Session 操作排空之后；Host 关闭先拒绝新操作、取消 Turn/Task，再等待 Session 操作结束并释放 Host 租约。
- 两个并发删除请求共享关闭认领，只有一个执行清理，另一个观察完成状态。
- Conversation / Session HTTP 与 WebSocket payload 未变；测试不调用真实模型或外部工具。

## 失败、修复与复测

按 TDD 先添加 Store、Runtime、API、Chat 和双 worker 回归用例。初始测试确认缺少 `SQLiteSessionLifecycleStore`、`SessionLifecycleManager` 及请求租约接入。实现后，针对租约状态机、清理失败重试、Host 关停期间续租，以及 SQLite 双 worker 等待在途 Chat 租约完成定向验证。最终全量复测为 **624 passed**。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `.venv/bin/pytest -q` | **624 passed**，1 条既有 Authlib 弃用警告，57.01 秒 |
| Ruff lint | `.venv/bin/ruff check .` | 通过 |
| 改动文件格式 | `.venv/bin/ruff format --check src/hostpilot/api/app.py src/hostpilot/orchestration/chat.py src/hostpilot/orchestration/state_services_host_plugin.py src/hostpilot/persistence/__init__.py src/hostpilot/persistence/ports.py src/hostpilot/persistence/sessions.py src/hostpilot/runtime/__init__.py src/hostpilot/runtime/host.py src/hostpilot/runtime/lifecycle.py tests/unit/api/test_app.py tests/unit/orchestration/test_chat_routing.py tests/unit/orchestration/test_state_services_host_plugin.py tests/unit/persistence/test_sessions.py tests/unit/runtime/test_host.py tests/unit/runtime/test_session_lifecycle.py tests/integration/test_session_lifecycle_workers.py` | 16 files already formatted |
| 全仓 mypy | `.venv/bin/mypy src/hostpilot` | 126 个源码文件，无问题 |
| 补丁空白检查 | `git diff --check` | 通过 |

关键回归包括：Store 两实例共享数据库与 Host 租约过期、deleted tombstone、防止 closing 后新访问、清理失败后重试、删除不再依赖 Conversation 行、远端 Session 租约排空、并发删除只运行一次清理、Turn 跨 Context/Model 工作持有租约、历史与 route-only 评测持有租约、单 Session API 拒绝 closing 状态、Host 停机等待本地租约释放，以及两个 SQLite worker 的真实协调。

## 限制

- SQLite Store 要求相关 ASGI worker 共享同一数据库；其他存储后端需实现 `SessionLifecycleStore` Port。该验证没有覆盖 PostgreSQL 或多主机网络文件系统。
- 崩溃 worker 的操作租约依赖 Runtime Host 租约到期后才不再阻止删除；过期窗口内保守等待。
- 清理回调必须保持幂等，才能安全重试部分完成的 Checkpoint、Memory 或 Conversation 清理。
- 此迁移协调 Session 生命周期，不恢复 Agent / Plan 执行游标，也不恢复模型或工具调用结果；相关工具结果未知时的安全处理见[单独报告](2026-11-21-unknown-tool-outcome-recovery.md)。
- 全量测试出现的唯一警告来自依赖导入 `authlib.jose` 的弃用提示，不是本次改动引入的失败。
