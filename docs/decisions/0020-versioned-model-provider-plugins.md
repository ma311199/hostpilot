# ADR-0020：版本化模型 Provider 插件

## 状态

已采纳

## 背景

`ModelProvider` 已定义统一的 `complete(ModelRequest) -> ModelResponse` 契约，但生产工厂固定创建 OpenAI 兼容实现。接入供应商原生 API、自托管推理或测试 Provider 时，需要替换构造方式，同时向 SkillEngine 和 Agent 框架提供一致的模型入口。

## 决定

- 用 `ModelProviderRegistry` 注册 Provider 实现与 Agent 框架模型适配器组成，接口版本为 `1.0`。
- 默认实现 ID 为 `builtin.openai-compatible`，由 `HOSTPILOT_MODEL_PROVIDER_PLUGIN` 选择。
- 自定义工厂必须返回 `ModelRuntimeComponents`，其 Provider 实现 `ModelProvider.complete`，并提供非空 `agent_chat_model`。
- 工厂通过 `create_app` 或 `create_configured_chat_agent_runtime` 注入的注册表注册；插件获得 `Settings`，包括进程内的密钥字段，因此仅信任应用启动代码。注册表不提供进程隔离。
- 所有 Provider 必须使用统一请求/响应契约，保留 HostPilot 预算控制及其 Token usage 统计；Provider 的错误需要映射至统一错误分类。
- 不支持动态下载、任意模块路径导入或热加载。

## 影响

- 可替换 Provider 和框架模型适配器的构造实现，现有 OpenAI 兼容实现行为保持默认。
- 新 Provider 需要负责供应商特有协议、认证、超时与错误映射；凭证仍不向模型、前端或持久配置暴露。

## 复核条件

加入模型配置 UI 中的 Provider 选择、独立认证方式或热切换前，复核非敏感配置持久化、Secret 生命周期、运行时重建及配置迁移策略。
