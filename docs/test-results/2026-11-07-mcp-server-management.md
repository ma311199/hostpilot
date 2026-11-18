# 2026-11-07 MCP Server 配置管理验证

## 实现范围

- 远程 Server 元数据在本地 SQLite 中新增、更新、启停和删除。
- API Key 只经本地配置 API 提交并保存在后端进程内存；读取接口只返回 `api_key_configured`。
- 重启后恢复连接元数据；需要密钥的启用项显示等待 API Key，不创建认证连接。
- 提交密钥后使用新配置重建 MCP Provider；新 Provider 生效后再关闭旧 Provider。
- 单个远程 Server 连接失败时保留其他可用 Server；错误状态不包含 URL 或凭证。
- 请求校验错误不回显输入值，MCP endpoint URL 拒绝常见凭证 query 参数。
- `HOSTPILOT_MCP_SERVERS` 环境配置继续支持，环境管理项在 UI 中只读。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 后端全量测试 | 254 passed |
| 后端 Ruff 检查与格式检查 | 通过，119 个文件 |
| 后端类型检查 | 通过，75 个源文件 |
| 前端测试 | 35 passed，10 个测试文件 |
| 前端 ESLint | 通过 |
| 前端类型检查与生产构建 | 通过 |

后端依赖输出一条 Authlib `authlib.jose` 弃用警告，与本次修改无关。
