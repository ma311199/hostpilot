# Model Provider 插件注册与替换记录

## 实现

- 新增版本 `1.0` 的 `ModelRuntimeRegistry`，按 `HOSTPILOT_MODEL_PROVIDER_PLUGIN` 选择 Provider 实现。
- 默认实现 ID 为 `builtin.openai-compatible`，保持原有 OpenAI-compatible Provider 行为。
- 自定义工厂返回 `ModelRuntime`，其中 Provider 必须提供 `complete(ModelRequest)`，同时提供非空 Agent 框架模型适配器。
- `create_app` 的初次启动与模型配置重建均沿用注入的注册表。
- 自定义工厂可读取 Settings 中的 SecretStr；插件只信任应用代码注册，不提供进程隔离。

## 验证

- 定向测试：Model factory、Settings、Runtime 和 API App 单元测试：78 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：330 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 87 个后端源文件通过。

## 后续

Provider 特定配置 Schema 和模型配置 UI 选择尚未实现；SQLite 持久化 Port 是下一候选替换边界。
