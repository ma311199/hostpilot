# SQLite 与 Markdown 持久化验证记录

- 日期：2026-10-29
- Python：3.11
- 依赖：DeepAgents 0.7.32、LangGraph 1.2.18、langgraph-checkpoint-sqlite 3.1.2
- 范围：SQLite 会话索引、LangGraph SQLite 检查点、Markdown 记忆存储、本机 FastAPI 会话 API

## 结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 完整质量检查 | scripts/check_quality.sh | 通过 |
| 格式 | Ruff format check | 47 files already formatted |
| Lint | Ruff check | All checks passed |
| 类型检查 | mypy src/hostpilot | Success: no issues found in 35 source files |
| 单元与集成测试 | pytest -q | 46 passed |
| 锁文件一致性 | uv lock --check | Resolved 113 packages |

## 覆盖范围

- SQLite 会话索引可由新 store 实例重开读取；数据库文件权限为当前用户可读写。
- LangGraph SQLite checkpointer 关闭后重开仍可恢复同一 thread 的状态，不同 conversation_id 状态互相隔离。
- Markdown 会话摘要与共享长期记忆互不影响；覆盖大小、路径穿越和符号链接会被拒绝。
- FastAPI 支持会话创建、列表、读取和删除；删除同时清除 graph checkpoint 与会话摘要；CORS 仅允许配置的前端源。
- API 默认绑定 127.0.0.1；DeepAgents Skill 适配器可接收持久 checkpointer。

## 已知缺口

- 尚未实现实际 Chat API 和 L0–L4 请求流程；当前 API 管理会话元数据。
- Context Builder 尚未把 SQLite 历史与 Markdown 记忆注入 DeepAgents 调用。
- Markdown MemoryWriteProposal 的策略审核与摘要生成流程尚未接入；存储适配器要求调用方负责授权和内容净化。
- 会话保留期限、备份、迁移和 Markdown 长期记忆的编辑/合并策略待定。
- CI 平台流水线仍未配置。
