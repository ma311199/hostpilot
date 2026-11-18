# 脚本目录

存放可复用的本地开发和维护脚本。新增脚本需说明用途、参数、运行环境及是否会修改数据；不要在脚本中硬编码凭证。

## 质量检查

在仓库根目录运行 scripts/check_quality.sh，依次检查格式、lint、类型、单元测试和 uv 锁文件一致性。脚本要求已安装 uv，并使用锁定的 dev 与 agents 依赖组；它不会修改依赖声明或锁文件。

## 记忆检索评测

从仓库根目录运行 `.venv/bin/python scripts/evaluate_memory_retrieval.py --char-budget 220`，比较固定顺序召回和本地 BM25 风格词项排序。评测数据位于 `docs/evaluations/memory-retrieval-v1.yaml`，仅含合成样例；脚本不请求模型、不读取用户记忆文件，也不修改数据。当前评测不包含 Embedding 或向量索引。

## 上下文指代真实模型评测

启动本机 HostPilot 后运行 `.venv/bin/python scripts/evaluate_contextual_reference.py --output /tmp/hostpilot-reference-resolution.json`。脚本调用 route-only API 和当前配置模型，数据位于 `docs/evaluations/contextual-reference-resolution-v1.yaml`。样例上下文仅作为有大小上限的单次请求输入，不写入会话或记忆；接口只调用 Skill Selector，不执行 Skill、Plan 或工具。报告只保存样例 ID、候选编号、状态、置信度、Token 与延迟，不含提示词或上下文正文。
