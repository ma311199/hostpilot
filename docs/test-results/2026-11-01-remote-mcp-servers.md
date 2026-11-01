# 多个远程 MCP Server 接入验证

- 验证日期：2026-11-01
- 变更范围：`HOSTPILOT_MCP_SERVERS` 配置、Streamable HTTP Client、工具发现与命名、ToolExecutor 审批治理
- 关联决策：[ADR-0008：远程 MCP Server 与 Streamable HTTP](../decisions/0008-remote-mcp-servers.md)
- 关联架构：[HostPilot 架构设计](../architecture.md)

## 验证内容

- 环境 JSON 配置可解析多个独立 Server；名称唯一、URL scheme 和 SecretStr 请求头经过校验。
- 每个 Server 创建独立 Streamable HTTP FastMCP Client；远端配置认证头不出现在设置对象 repr 中。
- 两个独立 MCP Server 暴露同名工具时，Agent 工具名带 Server 前缀，路由分别到对应会话。
- 远程工具通过 Tool Registry 和 ToolExecutor；无审批提供方时 fail-closed，审批通过后才执行。
- 请求凭证不进入模型工具 schema 或工具目录 API。

## 测试与质量检查

- 全量后端测试：`126 passed`。
- 配置模块定向覆盖率：`src/hostpilot/config/settings.py` 为 96%（11 项测试）。
- Ruff lint：通过。
- Ruff 格式检查：通过。
- mypy：49 个源文件无问题。
- FastMCP 导入产生一条 Authlib 弃用警告，未影响测试结果。
- MCP Provider 覆盖率未能采集：对工具测试运行 pytest-cov 时，FastMCP/Pydantic 导入阶段触发 `KeyError: 'pydantic.root_model'`，测试尚未开始。常规全量测试正常通过；需后续排查覆盖率工具与当前 Pydantic/FastMCP 组合的兼容性。

## 未覆盖范围与限制

- 未连接真实部署的远程 MCP Server。测试用独立的进程内 FastMCP Server 验证多 Client 会话和路由，并检查生产 Client 构造为 Streamable HTTP Transport；实际网络、TLS、认证服务和远端超时仍需联调。
- 当前通过环境变量 JSON 配置，不支持页面增删远程 Server；修改配置需重启 HostPilot。
- 每个远程工具默认要求逐次审批，尚无工具级只读策略配置。
