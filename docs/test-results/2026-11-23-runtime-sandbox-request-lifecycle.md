# Runtime Sandbox 请求级生命周期验证

## 范围

- 日期：2026-11-23
- 基线：`main`，HEAD `9469b67`；工作区还包含 Runtime Chat Turn 跨 Host 恢复的未提交改动。
- 目标：由 ChatService 读取 Conversation 持久 SandboxMode，将模式放进 RequestContext，并用稳定 ToolFacade 的异步作用域绑定 Provider/ContextVar 与清理；FastAPI 和 ChatAgentRuntimeHostPlugin 不再递归遍历 Runtime 对象图。
- 本次未运行真实模型评测；没有收集覆盖率数据。

## 实现验证

新增的 RequestContext 与 ToolFacade 测试在实现前按预期失败：RequestContext 拒绝 `sandbox_mode` 参数，ToolFacade 没有 `sandbox_mode_scope`。加入字段、Provider 模式重置、ContextVar Token 恢复及每个 Facade 的异步互斥锁后，定向测试通过。独立审查发现新字段曾改变第三个位置参数的含义；新增回归测试先复现审批 Provider 被静默丢弃，再将 `sandbox_mode` 放到 dataclass 字段末尾以保持旧构造器位置顺序。

ChatService 测试在实现前也按预期失败：Chat、PlanRun 恢复、Chat Turn 恢复没有读取持久 SandboxMode；无 ToolFacade 时没有设置只读 ContextVar。接入后，三个执行路径均将模式写入 RequestContext，恢复执行期间也使用同一模式。REST/WebSocket 入口现在委托 ChatService；API 测试覆盖 workspace-write 审批、read-only 拒绝、断开后的新只读会话和恢复取消。MCP 回归夹具使用 Runtime Host 实际持有的 Provider 与 ToolFacade。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/unit/runtime/test_context.py tests/unit/tools/test_facade.py` | 12 passed |
| `.venv/bin/pytest -q tests/unit/orchestration/test_chat_routing.py tests/integration/test_runtime_chat_turn_recovery.py` | 16 passed |
| `.venv/bin/pytest -q tests/unit/api/test_app.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/orchestration/test_tool_governance_host_plugin.py tests/unit/tools/test_mcp.py` | 122 passed，1 条 Authlib 弃用警告 |
| `bash scripts/check_quality.sh` | 748 passed，1 条既有 Authlib 弃用警告；Ruff 格式检查 233 个 Python 文件，lint 通过；mypy 128 个源码文件通过；`uv lock --check` 通过 |
| 前端 `pnpm test` | 18 个测试文件，93 passed |
| 前端 `pnpm lint` | 通过 |
| 前端 `pnpm build` | TypeScript 检查和 Vite 生产构建通过 |
| `git diff --check` | 通过 |

测试通过数仅说明这些代码路径的功能和回归断言通过，不代表模型质量或覆盖率。完整 pytest 数包含此前工作区中 Runtime Chat Turn 恢复改动的用例，不与上面定向测试数相加。

## 结果与边界

- ChatService 在 Session 租约内读取持久模式；SandboxMode 位于 RequestContext，不进入 ContextSnapshot 或模型提示内容。
- ToolFacade 的作用域序列化共享 Provider 模式修改，并在成功、异常和 WebSocket 断开时恢复 Provider 默认模式及 ContextVar。
- `model_components_lock` 仍保护运行中 Model/Provider 替换；`builtin.sandbox-backends` 仍管理 Runtime Host 级后端集合，两者与请求级模式作用域职责不同。
- 作用域锁按 ToolFacade 序列化共享 Provider 的请求；不同模式的同一 Host Chat 请求可能排队。逐工具显式 Sandbox 参数及无共享可变模式的并发执行不在本次范围内。

## 实施记录

Task 3 的 WebSocket 断开回归在移除 API 作用域前已经通过，因为此前 Task 1/2 已加入 ToolFacade 清理，而 FastAPI 的旧 `finally` 也仍在清理。保留了该回归，并结合 Facade 异常清理测试验证新职责边界；单个断开回归本身不能独立证明清理由哪一层拥有。
