# 2026-11-17：Runtime Host 生命周期验证

## 范围

验证进程级 `RuntimeHost` 的插件依赖、启动/关闭顺序、失败降级、后台任务取消、服务清理，以及 FastAPI lifespan 对聊天 Runtime 的托管。此次还检查 Host 适配器对路由 Selector 与既有 Runtime 调用契约的转发。

## 覆盖与结果

- 新增 `tests/unit/runtime/test_host.py`，覆盖依赖顺序、缺失依赖、依赖环、必需插件失败、可选插件失败、后台任务取消、服务注销和 Host 状态约束。
- 新增 FastAPI lifespan 集成用例，确认 Host 在应用运行期间为 `READY`，关闭时转为 `STOPPED`、注销服务并关闭 Agent Runtime。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**466 passed、2 failed**。默认 pytest 收集模式因 `tests/unit/contracts/test_models.py` 与 `tests/unit/routing/test_models.py` 同名而在收集阶段报模块冲突；使用 `--import-mode=importlib` 完成收集。
- 两个全量失败为文件沙箱写入用例和 MCP STDIO 启动用例。STDIO 用例单独重跑通过；文件沙箱用例单独重跑仍失败，隔离执行进程无法写入临时工作区，工具结果为 `verification_failed`。该失败发生在文件沙箱执行层，未涉及 Runtime Host 代码。
- Runtime Host、Chat Runtime 及相关 API 回归定向运行：**19 passed**。
- Ruff 检查通过；Runtime Host 与适配器定向 mypy 检查通过。全仓 mypy 仍报告既有 `src/hostpilot/evaluation/fixtures.py:39` 的 `step` 缺少类型注解；Host 内的类型错误已修复。

## 发现与修复

- Host 适配器原先即使 `confirmation_provider` 为 `None` 也传入该关键字，导致部分兼容 Runtime 抛出 `TypeError`。现在仅在存在确认 Provider 时传入该参数。
- Runtime Host 包装后，route-only 评测无法直接看到 Runtime 上的可选 Selector。适配器现在转发该属性；对应的两个路由评测 API 回归通过。
- 一个澄清 API 测试在退出 TestClient lifespan 后请求历史记录。Host 关闭时会正确关闭其 Runtime，因此将该历史读取移回应用生命周期内。

## 后续

- 文件沙箱失败留给沙箱专项排查，不计为 Runtime Host 缺陷。
- pytest 默认收集模式的同名测试文件冲突仍待处理；本轮未调整全局测试配置。
- 全仓 mypy 的 `evaluation/fixtures.py` 类型问题未纳入此次 Runtime Host 修复。

## MCP Provider 生命周期迁移复测

后续将 MCP Provider 的生命周期从 FastAPI lifespan 迁入 `builtin.mcp-provider` 插件，并声明 `builtin.chat-execution` 依赖该插件。运行时重配置由插件替换 Provider；Host 关闭时按依赖逆序关闭 Chat Runtime 和 Provider。

- 新增 `tests/unit/orchestration/test_mcp_provider_host_plugin.py`，验证服务注册、Provider 替换、旧实例关闭、新实例在 Host 停止时关闭。
- 同时复测 MCP 配置持久化、密钥重提交流程和 FastAPI Host 装配：定向测试 **18 passed**。
- 全量测试：`.venv/bin/pytest -q --import-mode=importlib`，**468 passed、1 failed**。唯一失败仍是文件沙箱写入用例；MCP STDIO 上一轮超时未复现，单独重跑通过。
- Ruff 与 Runtime/Provider 插件、Host 源码的定向 mypy 检查通过。API HTTP/WebSocket 路径未变。

此阶段仅迁移 MCP Provider 的服务持有与资源关闭。Tool Facade、Policy、Executor、Model、State、Memory 和 Sandbox 的装配尚未迁入插件。

## Turn 生命周期首轮接入与复测

`RuntimeContext` 现提供 `TurnLifecycleManager`，ChatService 在完成 Context 构建后登记每个 Turn，并在请求成功、异常或取消时写入终态。管理器可按 Turn ID、Conversation/Session ID 取消任务；RuntimeHost 停机时取消并等待活动 Turn 结束。终态历史为进程内有界元数据，不包含消息正文，也不持久化或恢复。

- 新增 `tests/unit/runtime/test_lifecycle.py`，覆盖 Turn 完成/失败/取消、按 Session 取消以及 Host 停机取消。
- FastAPI lifespan 集成用例确认 ChatService 的实际聊天请求进入 Turn 管理器。
- 定向测试：**22 passed**；Ruff 和 Runtime/Host/Chat/MCP Provider 相关源码的定向 mypy 检查通过。
- 全量测试最新结果：`.venv/bin/pytest -q --import-mode=importlib`，**471 passed、1 failed**。唯一失败仍为文件沙箱写入用例，失败发生在隔离执行进程写临时工作区时；不是 Turn 或 MCP Provider 生命周期回归。

当前 Turn 管理尚不覆盖 Agent/Session 状态机、持久化检查点、进程重启恢复或单独的 `Context → Model → Tool/Skill` 执行循环；这些仍按 ADR-0034 分阶段推进。
