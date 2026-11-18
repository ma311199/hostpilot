# ADR-0026：MCP 动态工具路由表

## 状态

已采纳

## 背景

`MCPToolProvider` 同时负责 MCP 连接生命周期、工具发现、路由映射、策略执行和工具调用。远程工具的暴露名称到 Server/原生工具 ID 映射属于动态目录数据，可以独立测试和替换；与连接建立、请求治理和 Tool Executor 的职责不同。

## 决定

- 新增 `RemoteToolRouteTable` Protocol 和默认 `MCPToolRoutingTable`。
- 路由表负责批量登记前校验名称冲突、解析暴露工具 ID，以及验证拦截器收到的 Server/原生工具 ID 是否与登记项一致。
- `MCPToolProvider` 接收可选 `routing_table`，未提供时创建默认实现；远程工具名称生成、发现和连接仍由 Provider 编排。
- 路由映射本身不授予执行权限。工具调用继续经过 Tool Executor 的参数校验、Policy、预算、审批、超时和结果验证。
- 注册批次在校验通过后整体写入；非法或冲突批次不得留下部分路由。

## 影响

- 路由表可单独测试，也可在受信任的应用启动代码中替换。
- 外部 MCP 工具的命名规则、API 和连接传输不变；不新增用户配置或插件注册表。

## 复核条件

增加非 MCP 工具 Provider、热更新连接或运行中 Server 重载时，复核路由表的移除、版本和并发更新语义。
