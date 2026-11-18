# 2026-11-16：Stage C `plan-006` Runtime 评测

## 首轮：30,000 Token

### 环境与命令

- 数据集：`stage-c-model-evaluation` v3，单例 `plan-006`。
- 模型：`gpt-6-luna`；Token 预算 30,000；工具调用预算 20。
- 模式：Selector、Plan Runtime 与治理工具的内存 stub；真实工具副作用为 0。
- 输出：`/tmp/hostpilot-stage-c-plan-006-runtime.json`。

### 结果

- 期望和实际路由均为 `plan_execute`，选择 `builtin/orchestrator_plan`。
- Planner 创建一个 `compare_api_contracts` 工具任务。任务本身成功，工具子 Agent 执行了 10 次搜索或读取；Plan Verifier 因 `verifier_BudgetExceededError` 未运行，因此 Runtime 最终状态为 failed，不能判作任务完成。
- 检索和读取范围包括仓库文件列表、`README.md`、`requirements.md`、`docs/README.md`、`docs/guide.md`、`ROADMAP.md`，以及三个说明性 fixture 文件：`src/hostpilot/orchestration/chat.py`、`src/hostpilot/routing/skill_selector.py` 和 `src/hostpilot/skills/model_plan.py`。这些 fixture 没有提供两个服务的 API 契约。
- 未调用写工具、未提交写入提案、未请求确认、无拒绝事件、无真实副作用。确定性工具边界探针通过。
- 提供方报告 Token **25,105 / 30,000**，耗时 **59.565 秒**。没有 Verifier 决策记录。

### 判断与下一步

路由和只读工具行为通过；任务验证未完成。当前轮的直接失败原因是 Verifier 预算耗尽。与此同时，隔离工作区没有两个服务的契约材料，因此提高预算后应允许系统明确说明证据不足并请求文件/服务名称，而不能编造差异。50,000 Token 复测见下节；两轮代码路径和预算不同，不作直接性能对照。

## 复测：50,000 Token

- 期望路由为 `plan_execute`，实际路由为 `clarify`；Selector 的原因码为 `skill_selection_unavailable`，澄清回复为泛化提示，没有询问服务名称或 API 契约文件。
- 未生成 Plan、任务或工具候选，工具调用和真实副作用均为 **0**。模型报告 Token **0**，耗时 **61.287 秒**。确定性工具边界探针通过。
- 这轮没有验证 Plan 或 Verifier，也不能判断材料缺失时 Planner 是否会提出具体澄清。相比首轮，这是路由模型未返回可用分类的独立失败；评测报告没有保留底层 provider 尝试错误，无法确认是超时、响应解析还是服务错误。
- 下一步先重跑一次并观察路由是否恢复；若再次出现 `skill_selection_unavailable`，应为 Runtime 评测增加 Selector provider 尝试诊断（错误类别/HTTP 状态），再定位模型调用失败原因，而不是继续提高 Plan Token 预算。

## 评测夹具修正

两轮运行确认 `plan-006` 的原始隔离工作区并未提供题目要求的两个 API 契约，导致任务缺少可比较证据。评测脚本现为 `plan-006` 单独加入两份合成 OpenAPI 文件：`services/customer-api/openapi.yaml` 与 `services/account-api/openapi.yaml`。两者共享 endpoint、参数与响应字段，但 `status` enum 分别包含 `suspended` 和 `disabled`，可供模型比较并引用文件证据；其他用例的 fixture 不变。

本修正经 Ruff 检查和格式检查通过。应在该夹具下再以 50,000 Token 运行一次 `plan-006`，验证模型能否找到两个契约、指出 enum 差异、提出有序修改计划并通过 Verifier。若再次返回 `skill_selection_unavailable`，再补充 Selector provider 尝试的错误类别与 HTTP 状态诊断。

## 合成契约夹具首轮运行：错误地在 Selector 阶段澄清

- 输出：`/tmp/hostpilot-stage-c-plan-006-runtime-contracts.json`；Token 预算 50,000，工具调用预算 20。
- 实际路由为 `clarify`，原因码 `missing_services_and_contracts`；期望路由为 `plan_execute`。助手具体询问服务名称和契约/代码位置，但没有创建 Plan、读取合成契约或调用工具。提供方报告 Token **4,010**，耗时 **7.103 秒**，真实副作用为 0。
- 该轮的澄清问题本身具体，但把可由工作区搜索发现的服务名与文件路径误当成必须由用户先给出的输入，因此不符合 `plan-006` 的仓库检查目标。

## 路由修正与本地验证

- Selector 现在会把“检查/比较 API 契约”请求交给 Plan 搜索工作区，不会仅因用户没有给出服务名或路径就提前澄清；分类模型仍判为 clarify 时，本地规则会按该明确任务意图转 Plan。
- 新增回归用例覆盖 `plan-006` 原提示被模型分类为 clarify 的情况。`tests/unit/routing/test_skill_selector.py` **29 passed**；Selector、评测脚本和测试文件 Ruff 检查及格式检查通过。
- 带新路由规则的真实模型复测待进行。验收应检查 Plan 是否读取两份合成 OpenAPI 契约、指出 `status` enum 差异、给出有序调整计划并由 Verifier 接受；若 Plan 无法找到材料，应具体说明证据缺失。

## 新路由后的真实模型复测：Planner 仍提前澄清

- 输出：`/tmp/hostpilot-stage-c-plan-006-runtime-plan-route.json`；模型 `gpt-6-luna`，Token 预算 50,000，工具调用预算 20。
- Selector 已选择 `builtin/orchestrator_plan`，原因码为 `workspace_api_contract_comparison`；Planner 返回空任务列表与澄清提示，询问用户提供服务和契约路径。最终用户可见路由为 `clarify`，与该用例预期 `plan_execute` 不符。
- 没有读取两份合成 OpenAPI 文件或调用工具；无写入、审批和真实副作用。提供方报告 Token **7,777**，耗时 **14.144 秒**。这不是预算或工具执行失败，而是 Planner 把可搜索发现的工作区信息当成前置输入。

## Planner 边界修正与本地验证

- Planner 指令现明确要求：工作区检查任务中的文件路径、服务名称和契约位置属于可发现输入；有搜索/读取工具时先创建任务查找，只有检查后仍缺证据或目标仍不明确时才澄清。
- 增加 Planner 指令回归断言；Plan 与 Selector 定向测试共 **51 passed**，相关源文件和测试文件 Ruff 检查及格式检查通过。
- 该规则的真实模型复测待进行。使用 50,000 Token 重跑，验收 Plan 先搜索并读取两个 OpenAPI fixture，比较 `status` enum（`suspended` 对 `disabled`），输出有序修改计划并由 Verifier 接受；不应写入文件。

## Planner 搜索规则后的真实模型复测：通过

- 输出：`/tmp/hostpilot-stage-c-plan-006-runtime-planner-search.json`；模型 `gpt-6-luna`，Token 预算 50,000，工具调用预算 20。
- 期望和实际路由均为 `plan_execute`，选择 `builtin/orchestrator_plan`。Planner 生成一个 `inspect-contracts` 工具任务；Task Executor 先搜索工作区，再读取 `services/customer-api/openapi.yaml` 和 `services/account-api/openapi.yaml`。任务成功，Plan Verifier 接受，理由 `goal_satisfied`。
- 响应准确指出 `GET /v1/customers/{customerId}` 的 200 响应中，`status` 枚举分别为 `[active, suspended]` 和 `[active, disabled]`；也列出已核对的一致字段，并给出按依赖排序的计划：确认业务定义/权威契约、统一枚举、检查实现/客户端/测试、运行验证。
- 响应没有臆定应保留哪个状态值，并说明 fixture 中没有服务实现或测试文件，需业务确认规范枚举。该限制是本次合成材料的范围，不是失败。
- 共 **5 次**只读工具调用（1 次搜索、4 次读取），写入提案 **0**、确认请求 **0**、真实副作用 **0**。提供方报告 Token **23,089 / 50,000**，耗时 **38.408 秒**；确定性工具边界探针通过。
- 终端中的“评估未通过并转交 Plan”表示任务经 Plan 回退处理；JSON 显示最终路由、Plan 任务及 Verifier 均成功，因此本例目标通过。该结果仅验证合成契约比较流程，不代表真实服务兼容性。
