# Runtime Chat Turn 跨 Host 恢复验证（2026-11-23）

## 范围

交付 Runtime-native Chat Turn 的跨 Host 显式恢复：用户查询 Session 中断 Turn 后，通过 ChatPanel 的恢复按钮发送 WebSocket `turn.resume`。服务端沿用原 Turn ID，从具有 Turn 关联标记的持久 Checkpoint 继续；不会重新写入原用户消息。恢复使用新 RequestContext 和预算，并重新经过当前授权、Policy、工具审批及工具结果恢复判定。

列表 API 只返回 Turn ID、开始时间、是否可恢复和安全原因码。查询与 Host 启动都不会派发恢复。WebSocket 复用现有逐工具审批协议。Turn 被其他 Turn 覆盖、Checkpoint 边界无法验证、兼容 `builtin.deepagents` 不支持恢复等情况均失败关闭。

工具调用按持久调用日志、请求指纹、稳定幂等键、Provider 能力和期限恢复；已 checkpoint 的结果复用，纯读允许重试，未知写在不能安全重放时继续等待人工核实。模型供应商已完成但响应未写入 Checkpoint 时，用户确认恢复后模型请求可能再次发送并产生额外费用，无法精确续接供应商侧的在途请求。

## 验证环境和命令

- 基准提交：`9469b67`；所有实现和文档改动保持未提交。
- Python：仓库虚拟环境，Python 3.11。
- 前端：仓库锁定依赖，Vitest 5.0.5、Vite 8.3.5。
- 全仓后端质量检查：`bash scripts/check_quality.sh`。
- 前端：在 `frontend/` 运行 `pnpm test`、`pnpm lint`、`pnpm build`。
- 差异空白检查：`git diff --check`。
- 未运行需要真实模型密钥的评测；本轮没有真实模型调用或外部副作用。

## 结果

- `bash scripts/check_quality.sh`：通过。Ruff 对 233 个 Python 文件格式检查通过，Ruff lint 通过；mypy 检查 128 个源码文件通过；全量 Python **739 passed**；`uv lock --check` 通过。
- Python pytest 有 1 条既有 Authlib `authlib.jose` 弃用警告，与本次改动无关。
- 前端 `pnpm test`：18 个测试文件、**93 passed**。
- 前端 `pnpm lint`：通过，0 warning / error。
- 前端 `pnpm build`：TypeScript 检查和 Vite 生产构建通过。
- `git diff --check`：通过。
- 测试覆盖率未测；没有从通过数推算覆盖率或模型质量。

## 覆盖内容

- Turn Store 的 Session 隔离、SQLite 重开读取、过期 Host 中断标记和原子单次认领。
- 工具日志按 Turn 查询、跨 Host 所有权交接、原调用身份与幂等键复用，以及工具结果门禁。
- Checkpoint Turn 关联、恢复边界验证、原消息复用、新 RequestContext 和新预算。
- REST 查询不派发；WebSocket 显式恢复、Session 隔离、逐工具审批、审批拒绝和断连后重新中断。
- 前端恢复列表、显式点击、恢复结果刷新、WebSocket 响应/错误解析和审批回调；恢复不会自动触发。
- 普通 Chat、Plan 恢复和其余 Python/前端测试全量回归。

## 过程发现

- 首轮 Ruff 检查发现两个既有修改文件需要格式化；修复后 lint 又发现导入顺序、异常链和超长行问题，均已处理。
- 首轮 mypy 暴露 `StoredTurn` 导入模块错误、恢复变量类型过窄，以及幂等键变量名与消息索引键冲突。修复后，全仓 mypy 与 Python 回归通过；幂等写调用现在传递账本生成的稳定字符串键。
- 最终完整后端质量检查和前端三项检查均重新执行并通过。

## 限制

- 供应商模型请求本身不可持久恢复；未 checkpoint 的模型响应在用户确认后可能重发。
- 显式 `builtin.deepagents` 执行路径的私有图状态不在此恢复契约内。
- 没有工具端有效幂等保证的未知写操作需由用户核实，不会自动重放。
- 未测 coverage 百分比或真实模型回答质量。

## 后续断连收尾修复（2026-11-24）

`test_recovery_websocket_disconnect_interrupts_without_continuing` 曾在 TestClient 退出时偶发收到 `CancelledError`。根因是两个 WebSocket 审批 Provider 将客户端断开转换成普通 `asyncio.CancelledError`，ChatService 正确将恢复 Turn 标记为 `interrupted` 后重新抛出，而 WebSocket endpoint 只处理原始 `WebSocketDisconnect`，导致 ASGI task 的取消从 TestClient 上下文冒出。

新增 `WebSocketDisconnectCancellation`，仍属于 `CancelledError`，因此 Turn/Task 生命周期照常记录取消/中断；Chat WebSocket endpoint 只捕获该客户端断连专用子类并正常返回，不吞掉 Host 或任务取消。Tool Confirmation 与 Plan Task Cache 审批 Provider 共用该类型。

- 断连恢复集成测试、Task Cache Provider 断连测试：**2 passed**。
- `test_recovery_websocket_disconnect_interrupts_without_continuing` 单独连续运行 10 次：**10/10 passed**。
- API 单测集合：**85 passed**。
- 全量 `bash scripts/check_quality.sh`：Ruff 格式检查 **234 files**、Ruff lint 通过、mypy **129 source files**、Python **751 passed**、`uv lock --check` 通过。
- 保留 1 条既有 Authlib 弃用警告；未运行真实模型评测。

### 发送阶段断连补充修复（2026-11-24）

补齐审批请求发送期间断连的处理：Tool Confirmation 与 Plan Task Cache Provider 现在会将 `send_json()` 和接收决策放在同一断连捕获范围内。发送期间的 `WebSocketDisconnect` 也会转换为客户端专用取消，沿用既有 Turn 中断收尾路径；超时和 Host 主动取消行为不变。

- 两个新增发送阶段断连回归测试先按预期失败（直接收到 `WebSocketDisconnect`），修复后通过。
- 两个发送阶段回归测试加恢复断连测试：**3 passed**。
- 全量 `bash scripts/check_quality.sh`：Ruff 格式检查 **234 files**、Ruff lint 通过、mypy **129 source files**、Python **753 passed**、`uv lock --check` 通过。
- 保留 1 条既有 Authlib 弃用警告；未运行真实模型评测。
