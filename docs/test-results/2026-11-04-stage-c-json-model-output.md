# Stage C 普通 Chat JSON 输出兼容记录

- 日期：2026-11-04
- 范围：Plan Planner/RePlanner、Plan Verifier、业务 Skill 准入与结果评估
- 架构依据：[新架构文档](../新架构文档.md)、[ADR-0009](../decisions/0009-skill-runtime-and-yaml-contract.md)、[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)

## 问题

配置的 OpenAI 兼容服务能处理普通流式 Chat Completions，但 Plan 请求触发 LangChain 的 `with_structured_output` / JSON Schema response format，供应商返回 400。Planner 因此在工具执行前失败，文件 action 尚未执行。

## 实现

- Planner/RePlanner、Verifier 和 `LLMSkillAssessor` 统一改用普通 `model.ainvoke` Chat 请求，在 system 指令中描述 Pydantic JSON Schema，并要求仅返回 JSON 对象。
- `ainvoke` 会在 LangChain 内部聚合流式响应；解析器接受普通字符串和文本 content blocks，因此模型以流式或非流式完成调用时，都从聚合后的文本解析 JSON。
- 本地执行 JSON 解码、Pydantic 校验、计划 Validator 校验；补充的字段、错误类型、非法 JSON 均 fail-closed。计划只有通过这些步骤后才进入调度和受治理的工具执行链。
- Token usage 仍从模型响应提取并计入请求预算；未改变 Skill 路由规则、Plan DAG、工具权限、审批或文件写入契约。

## 验证

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 201 passed |
| Ruff lint / 格式检查 | 通过，94 个源码与测试文件格式正确 |
| mypy | 61 个源码文件通过 |
| `git diff --check` | 通过 |
| 真实供应商联调 | 未执行 |

专项覆盖普通 JSON 字符串、流式聚合文本块、Pydantic Plan/Verifier/评估器解析、token usage 预算记账及分类失败时的 fail-closed 行为。

## 限制

- 单元测试验证了非流式字符串和聚合流式 content blocks 的解析；真实模型的输出质量、该供应商对长 system prompt 的限制仍需重启后联调确认。
- 无效或截断 JSON 会拒绝该结构化结果并进入现有失败处理；不会尝试执行未校验的计划。
- 选择器、Plan 模型和其他 JSON 结构化调用已不依赖供应商结构化输出 API；将来接入供应商原生 schema 接口应作为可选适配能力，不得成为当前兼容路径的必需条件。
