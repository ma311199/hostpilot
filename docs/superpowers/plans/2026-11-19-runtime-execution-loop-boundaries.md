# Runtime 执行循环边界实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Runtime Core 中建立可替换执行循环的请求、结果和 Model/Tool/Skill Port 契约，不改变当前 LangGraph 行为。

**Architecture:** 契约位于 `hostpilot.runtime.execution`，复用现有模型消息与工具提案类型，显式承载同一个 `RequestContext`。本切片仅建立接口与输入边界校验；LangGraph 继续是当前执行循环，MCP/ToolExecutor 仅能经治理 Port 被后续 Runtime 执行器访问。

**Tech Stack:** Python 3.11、dataclasses、StrEnum、typing.Protocol、pytest、Ruff、mypy。

**Spec:** `docs/superpowers/specs/2026-11-19-runtime-execution-loop-boundaries-design.md`

## Global Constraints

- Runtime 不持有具体模型供应商、MCP Client、ToolExecutor 或 Skill 框架实现。
- 每个执行请求持有单一 `RequestContext`；进程共享 `RuntimeContext` 不保存请求状态。
- 工具提案是不可信输入；Runtime Port 契约不得绕过 allowlist、预算、策略或审批。
- 当前 LangGraph、HTTP/WebSocket、ToolFacade、预算、审批、检查点行为保持不变。

## Review Focus

- 空消息、重复工具定义、无效或不匹配 allowlist 不能构造可执行请求。
- `max_steps` 必须是大于零的整数，并拒绝布尔值。
- 共享同一个 RequestContext 的身份不得被复制或重建。
- Runtime 包导入不得触发 LangGraph、MCP Provider 或具体模型 SDK 导入。

### Task 1: 定义执行请求/结果及 Port 契约

**Files:**
- Create: `src/hostpilot/runtime/execution.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Test: `tests/unit/runtime/test_execution.py`

**Steps:**
- [x] 先写请求校验、RequestContext 身份、结果终态及 Protocol 暴露测试。
- [x] 运行目标测试并确认因契约模块/类型缺失按预期失败。
- [x] 实现冻结请求/结果数据类、执行状态枚举和模型/工具/Skill/循环 Protocol。
- [x] 运行 Runtime 定向测试、Ruff 和 mypy。

### Task 2: 同步架构文档与完成回归

**Files:**
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/新架构文档.md`, `docs/roadmap.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-19-runtime-execution-loop-boundaries.md`

**Steps:**
- [x] 记录执行契约的边界、当前 LangGraph 保留现状和后续 State Port 依赖。
- [x] 搜索确认本切片没有改变生产调用路径，且 Runtime 导入不加载具体执行框架。
- [x] 运行全量 pytest、Ruff lint/format、全仓 mypy 和 `git diff --check`。
- [x] 将结果和本轮首测失败原因写入报告、指标台账及 SDD ledger。
