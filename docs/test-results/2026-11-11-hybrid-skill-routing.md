# 2026-11-11：独立路由模型与本地 Skill 向量索引验证

## 本轮实现

- 增加远端路由小模型的独立 Web 配置入口、API 和 SQLite 非敏感设置存储；API Key 只保留在后端进程内存，重启后状态显示需要重新输入。
- Skill 候选使用本地 BM25 与远端 Embedding 的 ChromaDB 持久索引双路召回。索引按 Skill 元数据哈希增量同步，Embedding 模型名用于区分集合。
- 路由裁判使用独立模型，不复用主聊天模型。业务快速路径必须同时满足 top1 `>= 0.85`、top1-top2 `>= 0.30`，且分数覆盖召回候选；否则进入 Plan。
- 路由前通过确定性规则结合最近任务焦点/会话摘要处理上下文指代；普通请求保留原文。续接请求使用最近任务焦点；代词请求仅在上下文中能提取唯一文件路径或引用文本时继续，否则直接澄清并跳过 BM25/Chroma 检索，不调用模型做查询改写。
- 缺少业务 Skill 必需槽位时，选择器仍保留已选 Skill 与部分输入，由 Skill Runtime 在 admission 和工作流步骤之前生成 Skill 专属澄清。
- ChromaDB 加入 `agents` 可选依赖组，`uv.lock` 已更新。

## 验证结果

- 后端路由、Skill Runtime、API、Runtime 与路由配置定向测试：106 passed。
- 前端 Vitest：17 个测试文件、68 passed。
- 前端生产构建：通过。
- Ruff、mypy、`uv lock --check`、`git diff --check`：通过。
- 本机虚拟环境尚未安装 `agents` extras 中的 ChromaDB，因此向量同步行为通过 Chroma 客户端替身测试；真实 ChromaDB 文件持久化仍需在安装该依赖后补做集成验证。

## 后续验证

- 使用真实 ChromaDB 运行本地索引重启恢复、Skill 更新/删除和 Embedding 模型切换验证。
- 配置真实 OpenAI 兼容的路由裁判与 Embedding 服务，执行脱敏业务路由评测，检查候选召回率、置信度校准、Gate 精度和全低 fallback。
