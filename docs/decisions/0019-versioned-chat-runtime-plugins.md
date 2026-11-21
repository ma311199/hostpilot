# ADR-0019：版本化对话运行时插件

## 状态

已采纳；默认实现由 [ADR-0035](0035-runtime-chat-default.md) 更新。

## 背景

当前对话运行时有稳定的 `ChatAgentRuntime` Port，但默认 LangGraph/DeepAgents 适配器由配置工厂直接构造。需要支持替换底层对话执行框架，同时保留上层 Skill 路由与 HostPilot 的应用服务契约。

## 决定

- `SkillEngineAgentAdapter` 保持 HostPilot 对话入口；仅替换它内部的 Chat Agent Runtime。
- 通过 `ChatAgentRuntimeRegistry` 按实现 ID 注册 `ChatAgentRuntime` 工厂，接口版本为 `2.0`。运行时实例必须实现 `read_messages`、`respond` 和 `record_turn`；`respond(message, request_context)` 接收 Runtime 的请求级 `RequestContext`。
- 原默认实现为 `builtin.deepagents`；当前默认实现及回退方式见 [ADR-0035](0035-runtime-chat-default.md)。插件仍由 `HOSTPILOT_CHAT_RUNTIME_PLUGIN` 选择。自定义工厂通过 `create_app` 或 `create_configured_chat_agent_runtime` 注入。
- 插件创建时仅接收模型适配器、checkpointer、Skill 适配器和工具列表，不负责 Skill 路由、工具授权或审批。
- 与 ADR-0018 一致，工厂属于受信任的进程内 Python 代码；注册表不提供安全隔离、下载或热加载。

## 影响

- 可替换底层对话框架，同时保持 ChatAgentRuntime 的消息读取、响应和记录接口稳定。
- `RequestContext` 在 Chat Agent 与 Skill 适配链中保持同一对象，包含请求快照、活动预算和可选审批 Provider。LangGraph 适配器在现有工具上下文边界映射为 `snapshot`、`budget` 和 `confirmation_provider`，包括将缺失审批 Provider 保留为 `None`。模型和工具 Port 暂保留现有参数形式，由各适配边界从请求上下文读取字段。
- 插件必须兼容 HostPilot 的请求上下文、预算和确认机制；接口兼容检查只验证必要方法存在，语义兼容仍由插件测试负责。旧版 `1.0` 插件因 `respond` 契约不兼容而被注册表拒绝。

## 复核条件

引入第三方分发、热加载或隔离运行时，复核 API 兼容、生命周期、资源关闭和不可信代码隔离设计。
