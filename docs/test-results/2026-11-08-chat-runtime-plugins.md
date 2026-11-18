# Chat Runtime 插件注册与替换记录

## 实现

- 新增 `ChatRuntimeRegistry`，接口版本 `1.0`，通过 `AgentRuntime` 的 `read_messages`、`respond`、`record_turn` 契约检查工厂结果。
- `builtin.deepagents` 注册为默认 Chat Runtime，可用 `HOSTPILOT_CHAT_RUNTIME_PLUGIN` 选择其他注册实现。
- 应用启动和模型配置重建均使用注入的同一注册表；Skill Runtime Adapter、Skill 路由与授权仍由 HostPilot 装配。
- 自定义工厂可以访问进程内模型、Skill、工具和 checkpointer 依赖，必须是受信任代码；未实现代码隔离。

## 验证

- 定向测试：Runtime、Runtime Plugin、Settings 与 API App 单元测试：75 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：328 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 87 个后端源文件通过。

## 后续

ModelProvider 当前有 provider-neutral 接口，但生产 Provider 构造仍固定；SQLite Stores 也没有统一的可替换 Port。按模块边界盘点逐项推进，不为所有内部类建立插件注册表。
