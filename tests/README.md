# 测试目录

- unit/：领域逻辑和模块内部行为。
- integration/：模块边界和外部适配器交互。
- security/：用户数据访问边界、授权拒绝、凭证保护和沙箱安全边界。
- fixtures/：合成或脱敏测试数据。

HostPilot 测试覆盖客户端 Agent 自身的安全和集成边界。独立 Agentic RAG 服务的多用户隔离及内部检索流程由该项目负责测试。执行策略和结果记录格式见 [测试策略](../docs/testing-strategy.md)。
