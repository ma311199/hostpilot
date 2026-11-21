# Runtime Chat Agent 生命周期验证

## 范围

将 Chat Agent 实例的租约、替换和关闭迁入 Runtime。每个 Turn 从读取历史到响应和记录使用同一代 Agent；配置替换立即发布新代，旧代等待在途租约释放后关闭；Host 停机拒绝新租约并清理 Agent。Session/Conversation/Turn 持久化和 API 契约不变。

## 修复与覆盖

- 新增 `AgentLifecycleManager`，使用代际租约、替换串行锁和异步条件变量协调活动 Turn、Agent 替换及 Host 关闭。
- `ChatService.send()`、route-only 评测和历史读取通过 Runtime 插件租用 Agent；ContextBuilder 可使用同一租用 Agent 读取消息历史。
- `ChatAgentRuntimeHostPlugin` 将 AgentLifecycleManager 注入 RuntimeContext，负责替换、停机清理，并转发当前请求的 SandboxMode。
- 回归覆盖在途 Turn 保持旧 Agent、后续 Turn 使用新 Agent、关闭延迟到租约释放、并发替换/关闭单次清理、替换调用取消后继续退役、关闭错误向调用者传播，以及 Host 停机拒绝新租约。
- API 集成回归发现生命周期接管后插件不再暴露旧 `_runtime` 属性，导致会话 SandboxMode 无法传至下游 Tool Provider。新增插件转发回归先失败（缺少 `set_sandbox_mode`），补充沿 Agent Provider 链传播后，该回归和真实 MCP 文件沙箱会话用例通过。
- 独立审查发现 Host 原停机顺序存在租约竞态：先取消 Turn、后拒绝 Agent 租约。新增 Host 级回归先失败（取消过程仍可获取新租约）；Host 现于进入停机阶段时先关闭租约入口，再取消 Turn，并由插件等待租约释放后清理 Agent。
- 插件替换回归验证配置刷新调用 `.replace()` 时新 Agent 立即可租用，旧 Agent 等待现有 Turn 释放后才关闭。模型配置事务的其他失败/回滚语义未在本次改动中调整。

## 验证

- Agent 生命周期、Runtime Host、ChatService、ContextBuilder、Runtime 插件和实际会话 SandboxMode 集成回归：**33 passed**。
- 全量后端：**608 passed**，1 条既有 Authlib 弃用警告。
- Ruff lint：`ruff check src tests` 通过。
- mypy：`mypy src/hostpilot` 通过，覆盖 **125 个源码文件**。
- 改动的 9 个 Python 文件 Ruff format 检查通过；`git diff --check` 通过。

## 结果与限制

Runtime 现在保证同一个 Chat Turn 不会在配置刷新期间混用 Agent，也不会因 Agent 关闭而中断仍持有租约的 Turn。新 Turn 使用已发布的新 Agent；旧代仅在其租约全部结束后关闭。Agent 创建与模型/框架组装仍由现有工厂负责。Session 状态机和 Agent 持久化恢复仍未迁移。

SandboxMode 仍由 API 请求入口设置在当前 Chat Agent/Provider 链上；插件提供受 Runtime Agent 生命周期管理的转发边界。Agent 的 `set_sandbox_mode` 目前仍是会话权限传播接口，API 通过现有模型配置锁串行化该状态的切换与聊天请求。
