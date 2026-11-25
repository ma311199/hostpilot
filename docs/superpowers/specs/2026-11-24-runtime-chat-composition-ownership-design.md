# Runtime Chat 组合实现归属迁移设计

## 状态

用户已批准；独立构建审查未发现需要补充确认的问题。本文只定义迁移边界，不包含实施步骤。

## 意图

把具体 Chat Runtime 组合实现从历史兼容入口 `create_configured_chat_agent_runtime()` 中迁出，使 Runtime 注册的 `builtin.chat-runtime-assembly` 服务调用唯一的组合实现。保留原函数、模块路径、公开导出和调用参数，作为兼容入口委托给新实现，避免破坏嵌入调用者和既有测试。

## 当前状态

- FastAPI 启动、MCP 刷新和主模型刷新已通过 `hostpilot.chat-runtime-assembly` 服务请求候选 Runtime。
- `ChatRuntimeAssemblyHostPlugin.build_candidate()` 仍动态导入并调用 `hostpilot.orchestration.runtime.create_configured_chat_agent_runtime()`。
- 兼容工厂位于 `orchestration/runtime.py`，负责组装 Model Runtime、Runtime-native 执行循环、Chat Runtime 插件、YAML/Markdown Skill 执行器、Selector、Plan Executor 与 `SkillEngineAgentAdapter`。
- 工厂内部的工具描述转换辅助函数及无 Provider Tool Port 也只在该组合实现中使用。仓库内现有外部引用主要为公共导出、兼容契约测试及 API 测试 monkeypatch；用户可能存在未被仓库搜索到的导入方。
- 当前候选 Settings 已在 `ChatRuntimeAssemblyRequest` 中深拷贝；Agent 发布、代际租约和旧代关闭由 `builtin.chat-execution` 管理。

## 目标与边界

- 新增专用组合模块 `hostpilot.orchestration.chat_runtime_composition`，将现有工厂中的具体构建逻辑和仅供其使用的辅助代码原样迁入。
- `ChatRuntimeAssemblyHostPlugin` 通过注入或直接引用新组合模块的构建函数完成候选 Runtime 创建，不再导入或调用兼容工厂。
- `create_configured_chat_agent_runtime()` 保留原位置、签名、默认值、返回契约与导出；函数体收敛为将原参数转交给新组合模块的兼容包装。
- 保持候选构建纯粹：不注册/发布 Agent、不替换 Provider、不持有配置 Store，也不负责候选资源的关闭；这些职责继续属于 API 配置协调和 `builtin.chat-execution` 生命周期。
- 保留 Settings、工具集合、ToolFacade、MemoryController、补偿/取消/Trace 服务、Skill 执行模式和 Model/Chat/Plan 注册表等现有组合输入及转发语义。
- 保持默认 `builtin.runtime-chat`、兼容 `builtin.deepagents`、Skill/Plan 路由、预算、审批、工具治理、检查点和恢复行为不变。
- 不拆分 Model、Skill、Plan 生命周期插件，不移除旧公共函数，不改 HTTP/WebSocket 或配置契约，也不运行真实模型评测。

## 考虑过的方案

1. **将全部组合代码直接塞入 Host Plugin。** 这会让一个插件同时承载生命周期接线与大型对象构建函数，也会让兼容包装器依赖插件实现，形成不必要的生命周期耦合，因此不采用。
2. **提取专用组合模块，由装配插件调用；旧函数委托给同一个实现。** Runtime 服务是应用默认调用入口，组合模块保留可独立测试的同步构建逻辑，兼容函数只保留历史 API 形状。选择此方案，因为它完成实现归属迁移，同时避免把无状态组合函数误作有独立生命周期的 Host 资源。
3. **保持现状，只将当前委托命名为内部 helper。** 这不会改变兼容工厂继续控制默认 Runtime 装配的事实，无法完成此次迁移目标。

## 结构与调用流程

```text
FastAPI lifespan / 配置刷新
  └─ hostpilot.chat-runtime-assembly
       └─ ChatRuntimeAssemblyHostPlugin.build_candidate(request)
            └─ chat_runtime_composition.build_configured_chat_agent_runtime(...)

历史嵌入/测试调用方
  └─ orchestration.runtime.create_configured_chat_agent_runtime(...)
       └─ chat_runtime_composition.build_configured_chat_agent_runtime(...)
```

组合模块接收当前兼容工厂原有的全部参数，并沿用同步构建与异常传播行为。Host Plugin 继续解析 Host-owned 的 Checkpointer、Trace 和 Cancellation 服务，再把这些服务与请求级候选输入传给组合函数。候选失败时由现有调用方关闭候选 Provider；Plugin 不发布、不关闭候选 Agent。

## 兼容与测试要求

- 旧函数保持在 `hostpilot.orchestration.runtime` 中，并继续从 `hostpilot.orchestration` 导出；`inspect.signature` 及现有调用方式不变。
- Plugin 测试应证明其构建通过新组合入口，且旧兼容符号被替换/监视时不会影响 Plugin 默认构建路径。
- 兼容工厂测试应证明旧入口把所有显式参数转给新组合实现，并保留构建结果和异常。
- 现有 Runtime 组合行为测试继续验证模型、执行循环、工具定义、Skill、Plan 与适配器的连接；必要时拆分纯构建 fixture，但不降低断言覆盖。
- API 测试从 monkeypatch 旧工厂切换为注入或替换新组合模块，继续验证初始装配与 MCP/模型刷新参数来源、失败不发布和资源关闭。
- 验证静态导入方向无循环依赖；新组合模块可使用 Runtime/Chat 契约，但 `runtime.py` 的兼容入口只能向组合模块委托。
- 更新 ADR-0034、Runtime 路线/架构文档、测试目录和指标台账，明确兼容入口与新组合实现的分工。

## 非目标与已知限制

- 本次不移除 `create_configured_chat_agent_runtime()`。仓库搜索不能证明没有仓库外消费者，因此保留兼容入口。
- 不处理配置持久化成功失败后的候选 Agent 关闭缺口；该资源回收限制早于本迁移存在，保持独立跟踪。
- 不改变 API 仍负责候选 MCP Provider 构造及多资源切换的职责。
- 不将 DeepAgents 私有执行状态纳入 Runtime Chat Turn 恢复契约。
- 组合实现仍是可信进程内 Python 代码；本次不引入进程隔离或第三方插件安全边界。

## 验收标准

- Runtime 装配插件不再引用或调用兼容工厂；默认和配置刷新路径都通过新组合模块构建候选。
- 旧函数和包级导出保持原签名与语义，并委托给唯一组合实现。
- Runtime、Skill、Plan、工具、预算、审批、生命周期和恢复回归保持通过。
- 不新增外部依赖，不改前端或对外 API。
- 依照仓库质量脚本执行全量 Python 检查，按本次触及的边界执行前端检查（仅在出现前端改动时），并运行 `git diff --check`；准确结果记录到测试报告与指标台账。
