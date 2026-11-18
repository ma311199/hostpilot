# 模型与 MCP 配置 Store Port 实现记录

## 实现

- 新增 `ModelConfigurationStore` 与 `MCPConfigurationStore` Protocol，应用不再直接依赖相应的 SQLite Store 类型。
- `create_app` 支持独立注入 `model_configuration_store_factory` 和 `mcp_configuration_store_factory`；默认实现不变。
- Model Store 契约只接受模型名、Base URL 与 streaming；MCP Store 只接受非敏感配置和凭证字段名称，不接收密钥值。
- 架构总览、模块职责、开发指南和 ADR-0022 已同步。

## 验证

- API App 单元测试：54 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：331 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 88 个后端源文件通过。

## 后续

Harness、Workspace、Audit、Cancellation、Trace 和 Skill 执行配置 Store 暂未抽象；依实际替换需求逐项处理。
