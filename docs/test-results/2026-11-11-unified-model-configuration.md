# 2026-11-11：统一模型配置与降级策略

## 实现

- 顶部只保留一个“模型配置”入口，表单按 LLM Model、轻量 LLM Model、Embedding Model、Reranker Model 排列。
- LLM Model 是必需项；其他角色可选。每个角色独立配置模型名、Base URL 和 API Key；密钥只保存在后端进程内存。
- 轻量 LLM 缺失或调用失败时使用主 LLM；Reranker 调用失败时依次回退到轻量 LLM、主 LLM。
- Embedding 缺失或服务失败时使用 BM25 词项召回。预算耗尽不会触发额外模型回退调用。
- SQLite 保存可选模型角色名称和各自的 Base URL，不保存 API Key；重启后按角色重新输入 API Key。角色密钥不会复用主 LLM 或其他角色的凭证。
- 旧版共享 Base URL 会在 SQLite 初始化时迁移到三个可选角色的独立 Base URL 字段。

## 验证

- 独立角色凭证修订后，后端 API 与配置迁移定向测试：59 项通过；包括 API Key 不跨角色复用、响应序列化隐藏密钥、旧共享 URL 迁移。
- 前端 Vitest：17 个测试文件、69 项通过；生产构建及 TypeScript 类型检查通过。
- Mypy：98 个后端源文件通过；相关 Ruff 检查及 `git diff --check` 通过。
- 后端定向验证：`tests/unit/routing`、路由模型配置持久化、Runtime 与 API 测试。
- 前端 Vitest：17 个测试文件、69 项通过。
- 前端生产构建及 TypeScript 类型检查通过。
- Ruff 检查和 Mypy（98 个后端源文件）通过。

## 限制

- 自动化验证覆盖了配置入口和模型回退逻辑。主模型已通过页面配置，并用于[在线路由冒烟评测](2026-11-11-live-route-smoke.md)；可选路由角色的真实服务调用和完整 selector-only 质量评测仍未覆盖。
- Embedding 使用 OpenAI 兼容 Embeddings API；Reranker 使用 llama.cpp `/v1/rerank`，由轻量 LLM 或主 LLM 独立进行准入置信度判断。
