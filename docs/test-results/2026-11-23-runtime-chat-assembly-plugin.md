# Runtime Chat 装配插件迁移验证

## 变更范围

- 新增 `builtin.chat-runtime-assembly`，通过 `hostpilot.chat-runtime-assembly` 提供候选 Chat Runtime 组装。
- 插件在 Host setup 中从 State Services 与 Application Stores 解析 Checkpointer、Trace 和 Cancellation；候选 Settings、ToolFacade、工具集合、MemoryController 和 Skill 模式按次显式传入。
- FastAPI 启动、MCP 配置刷新和主模型配置更新均调用装配服务。API 仍负责配置持久化、候选 Provider 创建和多资源协调切换；`builtin.chat-execution` 仍管理 Agent 代际与在途租约。
- `create_configured_chat_agent_runtime()` 保留为组合实现兼容入口，API 不再直接调用它。
- DeepAgents 私有状态恢复不在本次迁移范围内。

## 行为覆盖

- 装配插件校验所需 Runtime 服务、注册服务 ID，并将显式候选输入与 Runtime 服务转交给组合工厂。
- 初始模型装配、MCP Provider 刷新和主模型配置刷新都经装配服务；测试验证候选 Settings、工具和 Provider 变化被传入。
- 候选装配失败时当前 Chat Runtime 与 MCP Provider 保持不变。
- 自定义 Chat Runtime 工厂与无模型启动分支沿用现有行为。

## 验证

- `.venv/bin/pytest -q tests/unit/orchestration/test_runtime_plugins.py`：**9 passed**。
- Runtime/API 组合回归：`.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/api/test_app.py`：**97 passed**。
- `bash scripts/check_quality.sh`：Ruff 格式检查 **234 files**、Ruff lint 通过、mypy **129 source files** 通过、全量 Python **751 passed**、`uv lock --check` 通过。
- `pnpm test`（在 `frontend/` 运行）：**93 passed**；`pnpm lint`、`pnpm typecheck` 和 `pnpm build` 通过。
- `git diff --check`：通过。
- 未运行真实模型评测。Python 测试输出有一条既有 Authlib 弃用警告；运行时装配相关测试本身通过。
- 后续复跑发现恢复 WebSocket 断连的 TestClient 收尾问题，已在 2026-11-24 通过专用取消子类修复；详情见[Runtime Chat Turn 恢复验证](2026-11-23-runtime-chat-turn-recovery.md#后续断连收尾修复2026-11-24)。

## 过程记录

- TDD 首轮装配服务测试因模块不存在而按预期失败；实现后 **2 passed**。
- API 集成测试首轮因服务尚未注册而按预期失败；注册插件并改用服务后通过。
- 全量质量脚本先发现新文件格式和导入排序问题；修正后再次执行通过。
- 一个既有 Sandbox 后端集成测试的 monkeypatch 指向已不再由 `hostpilot.tools` 导出的 `mcp` 属性。测试现直接 patch `hostpilot.tools.mcp` 模块，原有后端复用断言通过。
- 独立终审建议补充 Settings 深层快照、候选 Provider 关闭和无模型/自定义工厂不调用装配服务的专门断言；已在 2026-11-24 完成，见下方补充记录。

## 后续契约补强（2026-11-24）

- `ChatRuntimeAssemblyRequest` 深拷贝 Settings，避免嵌套 MCP 凭证映射等可变对象在请求创建后被调用方修改，污染候选配置快照。回归测试先失败，再验证快照隔离。
- 候选装配失败测试现在验证失败候选 Provider 已关闭，原活动 Provider 和 Chat Runtime 仍保持运行。
- 增加无主模型与自定义 Chat Runtime 工厂两条启动路径的断言，确保两者都不会调用默认装配服务。
- 装配定向回归：**7 passed**。`bash scripts/check_quality.sh`：Ruff 格式检查 **234 files**、Ruff lint 通过、mypy **129 source files**、Python 全量 **756 passed**、`uv lock --check` 通过。
- `git diff --check` 通过。保留 1 条既有 Authlib 弃用警告；未运行真实模型评测，未单独测量覆盖率。

## 后续

兼容工厂仍承载具体组合实现，但不再由 FastAPI 配置路径直接调用。若后续希望完全移除该工厂入口，应先确认外部/测试导入点，再单独迁移组合代码并评估兼容策略。
