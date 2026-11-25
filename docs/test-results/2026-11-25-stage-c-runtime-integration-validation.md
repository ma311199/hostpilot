# Stage C Runtime Chat/API 集成验证

## 范围

- 日期：2026-11-25
- 基线：`9469b67`
- 目标：从 Chat API/WebSocket 入口验证 YAML/Markdown Skill 执行、失败转 Plan、任务工具授权及治理链路。
- 测试使用临时目录、脚本化 Model Provider 和内存工具 handler；没有调用外部模型或服务，没有读取模型密钥。
- 本报告只记录确定性集成契约，不代表真实模型路由或 Plan 生成质量。

## 覆盖路径

- YAML Skill 经真实 `SkillEngine` 执行；Markdown Skill 经 Markdown adapter 执行，不进入 YAML Engine。两条路径均验证响应、聊天历史和同一个 `RequestContext`。
- 注入一个可复现的 Skill 执行失败，经过 Chat WebSocket 进入真实 Plan planner、validator、task executor 和 verifier。失败原因及候选 Skill ID 保留在 Plan 请求中。
- Skill manifest 只授权 `submit_summary`。Plan 子 Agent 的工具定义只包含该工具；内存 Provider 经 `ToolFacade` 进入真实 `ToolExecutor` 和 `PolicyEngine`。
- 高影响操作通过 WebSocket 发出审批请求；测试明确批准后，只有内存 handler 执行一次。预算对象和审批能力贯穿到受治理 Provider。
- 响应和 Plan 结果经 Chat API/WebSocket 返回并写入会话历史。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| YAML 与 Markdown Skill 定向用例 | **2 passed** |
| Skill 失败转 Plan 单用例 | **1 passed** |
| `.venv/bin/pytest -q tests/integration/test_runtime_chat_skill_plan_pipeline.py` | **3 passed**，1 条既有 Authlib 弃用警告 |
| `bash scripts/check_quality.sh` | Ruff 格式检查 **236 files**；Ruff lint 通过；mypy **130 source files**；Python 全量 **763 passed**，1 条既有 Authlib 弃用警告；`uv lock --check` 通过 |
| `git diff --check` | 通过 |

全量测试结果中的 763 是 Python 功能测试通过数，不是模型质量分数或代码覆盖率。覆盖率未收集。无前端改动，本次未运行前端测试。

## 真实模型评测交接

真实模型执行尚未进行。用户可在本机应用配置和模型服务可用时，手动运行一个 Plan 用例：

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py --case-id plan-001 --show-response --output /tmp/hostpilot-stage-c-plan-001-2026-11-25.json
```

该命令未由本轮执行。请保留本机输出中的非敏感模型标识、用量、耗时、路由/Plan 结果和安全断言；不要发送或记录 API Key。收到结果后再更新真实模型质量结论。

## 限制

- Skill 失败由测试替身确定性注入；这验证失败后的接线和数据传递，不测真实模型产生失败的概率。
- 脚本化响应固定了计划和工具选择，不衡量 Planner、Verifier 或模型路由质量。
- 工具 handler 只写入进程内列表；真实文件、MCP Server 和外部副作用未测试。
- 现有 Authlib `authlib.jose` 弃用警告与本次改动无关。
