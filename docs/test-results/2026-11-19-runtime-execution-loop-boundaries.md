# Runtime 执行循环边界验证

- 日期：2026-11-19
- 范围：新增 Runtime 执行请求/结果、Model/Tool/Skill/Loop Port 契约和边界校验；当前 LangGraph 执行路径保持不变。
- 代码版本：当前工作区未提交改动；没有独立提交号。
- 环境：Python 3.11、pytest、Ruff、mypy。
- 数据集：不适用；本轮为请求契约与模块隔离自动化检查。

## 改动和覆盖边界

- `RuntimeExecutionRequest` 持有原 `RequestContext`，并校验非空 Model 消息、唯一工具 ID、工具 allowlist 子集和正整数 `max_steps`。
- `RuntimeExecutionResult` 统一返回终态、响应文本、规范化消息、Token 用量和循环步数；取消仍通过 Python task cancellation 传播，不伪装成完成结果。
- 定义 `RuntimeModelPort`、`RuntimeToolPort`、`RuntimeSkillPort` 和 `RuntimeExecutionLoop` Protocol，均放在 `hostpilot.runtime`，不依赖 MCP 或具体 Agent 框架。
- 工具执行边界接收模型产生的 `ModelToolCall` 提案；后续实现必须通过既有治理 Tool Port。
- 本切片没有循环实现或生产接线。当前实际循环仍在 LangGraph 图中，HTTP/WebSocket、预算、审批、工具策略和检查点行为不变。

## TDD 与失败原因

- 新增的 Runtime 契约测试首次运行时因 `hostpilot.runtime.execution` 模块不存在而按预期收集失败。
- 添加契约模块与 Runtime 包导出后，边界测试转绿；没有发现额外生产故障。
- 最终自审发现请求可传入 `None` Context；新增测试先以“未抛出异常”失败，再在请求构造时增加 `RequestContext` 类型校验。结果状态也限制为 Runtime 终态枚举。
- 初次 Ruff 检查提示新模块导入顺序和行宽问题，按规则格式化后通过。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/unit/runtime` | 21 passed |
| `.venv/bin/pytest -q` | 517 passed，1 条 FastMCP/Authlib 弃用警告 |
| `.venv/bin/ruff check src tests scripts` | All checks passed |
| `.venv/bin/ruff format --check src tests scripts` | 207 files already formatted |
| `.venv/bin/mypy src/hostpilot` | Success，117 个源码文件无问题 |
| Runtime 契约导入隔离检查 | 通过；未加载 LangGraph、DeepAgents、LangChain、MCP 或 OpenAI SDK |
| `git diff --check` | 通过 |

## 限制与后续

- 功能测试通过数不代表 Agent 质量、循环成功率或性能指标；本轮没有模型质量和性能测试。
- Runtime-native 循环尚未接管实际请求。接入前需要设计消息状态和 checkpoint State Port 所有权，并实现受预算、allowlist、工具治理、审批、取消和步数上限约束的执行器。
- Agent/Session 持久化状态和恢复仍未迁入 Runtime Core。
