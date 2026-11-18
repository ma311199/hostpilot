# Embedding 与 Reranker API Key 本地持久化验证

- 日期：2026-11-13
- 范围：路由模型配置 SQLite Store、API 重启恢复、角色 Key 清理与模型配置页面
- 数据：合成测试凭证；未读取或记录用户实际 API Key

## 实现

- SQLite 路由模型配置表新增 `embedding_api_key` 和 `reranker_api_key`，初始化时为既有数据库增加缺失列。
- PUT 配置会保存两个角色的 Key；省略 Key 时保留已保存值，移除模型角色时一并清除该角色 Key。
- 应用启动时从本地数据库恢复两类 Key 并重建可选模型客户端。主 LLM、轻量 LLM 和 MCP 凭证生命周期未改变。
- GET 与 PUT 响应只返回角色 Key 已配置状态，不返回 Key 值。Key 不进入前端存储、日志、Trace 或模型上下文。
- 本机 SQLite 中的两个 Key 为明文存储，没有额外加密；数据库文件权限设置为仅当前账户读写（`0600`），适用范围限定为用户本机模型服务。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| Store 保存、读取与旧表迁移 | 通过 |
| API 保存后关闭并重建应用，恢复两个 Key | 通过 |
| 配置 API 响应不包含 Key 值 | 通过 |
| 删除 Embedding 角色时只清除对应 Key，保留 Reranker Key | 通过 |
| SQLite 数据库文件权限为 `0600` | 通过 |
| Python 定向测试 | 60 passed |
| ModelSettings 前端测试 | 69 passed |
| Ruff、格式检查、Mypy | 通过 |
| 前端生产构建与类型检查 | 通过 |

## 用户操作

现有服务尚未运行这版代码，旧进程中的 Embedding/Reranker Key 仍只在内存，无法由新实现回收。重启加载新版代码后，请在模型配置页重新输入这两项 API Key 并保存一次；之后重启会自动恢复，页面仍不会回填 Key 明文。
