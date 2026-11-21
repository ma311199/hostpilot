# Runtime Model/Tool 执行循环验证

- 日期：2026-11-19
- 范围：实现受限 Runtime Model/Tool 循环及现有 ModelProvider、ToolFacade 适配器；保留默认 LangGraph Chat Runtime 和存储路径。
- 代码版本：当前工作区未提交改动；没有独立提交号。
- 环境：Python 3.11、pytest、Ruff、mypy。
- 数据集：不适用；本轮为合成模型响应和内存工具 Port 的自动化功能测试。

## 行为与覆盖

- `DefaultRuntimeExecutionLoop` 使用传入的完整消息作为上下文，只返回本轮追加的 Assistant/Tool 消息；Chat/Session 适配器继续拥有历史加载和持久化。
- 每轮模型请求前检查同一 RequestContext 的活动预算；ModelProvider 适配器使用 `complete_with_budget` 限制生成 Token 并记账，循环遵守 `budget_usage_accounted`，不会重复扣除。
- 模型工具定义仅发送 allowlist 中的条目；每批提案在调用 Tool Port 前整体校验工具名、调用 ID 和参数 Mapping。无效批次不会执行任何工具。
- 有效工具按序通过 ToolFacade 适配器执行；审批 Provider 和活动 RequestBudget 原样传递，工具结果按 `tool_call_id` 回填供下一轮模型读取。
- `max_steps` 限制模型调用轮数；最后一轮若仍提出工具调用，不执行新副作用并以 `MAX_STEPS_REACHED` 返回。取消和模型/工具/预算错误继续抛出。
- Skill/Plan 仍通过原有 SkillEngineAgentAdapter 路径处理；本循环没有直接注册 Skill 调用，也未接入默认 Chat Runtime。

## TDD 与失败记录

- Loop 测试首跑因 `hostpilot.runtime.execution_loop` 尚不存在而按预期收集失败；实现后 Loop 测试 **10 passed**。
- Adapter 测试首跑因 `hostpilot.runtime.adapters` 尚不存在而按预期收集失败；实现后 Model/Tool 适配器测试 **2 passed**。
- 自审发现达到 `max_steps` 时初版结果包含尚未执行的 Assistant 工具提案；新增断言先失败，再修正为不把未执行提案返回给上层，避免保存不完整的 Tool call/message 对。
- Runtime 全部定向测试：**33 passed**。
- 早期静态检查发现 Adapter 的 Protocol import 未使用以及格式问题；清理后复核通过，无未解决的代码失败。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/unit/runtime` | 33 passed |
| `.venv/bin/pytest -q` | 529 passed，1 条 FastMCP/Authlib 弃用警告 |
| `.venv/bin/ruff check src tests scripts` | All checks passed |
| `.venv/bin/ruff format --check src tests scripts` | 211 files already formatted |
| `.venv/bin/mypy src/hostpilot` | Success，119 个源码文件无问题 |
| Runtime contract import isolation | 通过；未加载 LangGraph、DeepAgents、LangChain、MCP 或 OpenAI SDK |
| `git diff --check` | 通过 |

## 限制与后续

- 执行循环现已具备运行实现，但尚未接入生产 Chat Runtime；当前用户请求仍由 LangGraph 执行。
- 循环不执行 Skill/Plan action，不组装现有 System/Memory Prompt，也不保存消息；接入前需由 Chat Runtime 插件提供这些适配边界。
- 不支持同一请求内并行工具调度、循环级自动重试和流式事件；没有测模型质量、端到端延迟或生产效果。
