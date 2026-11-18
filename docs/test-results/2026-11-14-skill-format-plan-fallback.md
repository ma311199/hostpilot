# 2026-11-14：Skill 格式分流、Plan 兜底与按需工具检索

## 范围

- 唯一高置信 Skill 按选中的格式直接执行：`skill.yaml` 进入 Skill Runtime，`SKILL.md` 进入 DeepAgents。
- 路由评估不一致或多个 YAML Skill 置信度大于 `0.5` 时转 Plan；业务候选按分数排序，最多传入前五个。
- YAML 准入/结果评估要求升级、Skill 执行失败、DeepAgents 异常或 Markdown 结果评估不通过会转 Plan，并传递失败原因及候选 Skill。
- Plan 先使用候选 Skill 能力分解目标；Planner 只接收抽象工具检索能力。未覆盖的工具任务按目标从当前工具目录检索最多五个候选。
- 前次副作用结果不确定时，Plan 只允许使用声明为只读的 YAML Skill、检索类工具和 `read_text_file`；先核对当前状态，再决定后续动作。

## 验证结果

- 全量测试：`pytest -q --import-mode=importlib`，**438 项通过**。
- 路由、DeepAgents 结果评估、Runtime 装配和 Plan 相关定向测试：**57 项通过**。
- Ruff 检查与格式检查通过；Mypy 检查 5 个后端源码文件通过。
- 集成测试出现一条已知 Authlib `authlib.jose` 弃用警告，不影响通过结果。
- 普通 pytest 导入模式会因 `tests/unit/contracts/test_models.py` 和 `tests/unit/routing/test_models.py` 同名模块而在收集阶段失败；`importlib` 模式可完整运行。

## 边界

- 置信度 `>0.5` 的前五个候选规则已用单元测试覆盖；真实模型路由质量和默认 Chat API 的在线端到端质量仍需单独评估。
- 当前工具候选召回复用字段化 BM25，对每个工具任务最多暴露五个候选；最终调用仍由 Tool Executor 执行策略、预算和审批校验。
- 取消请求继续沿用取消协议，不会伪装成普通 Skill 失败或自动触发 Plan 重试。

## 后续真实模型评测状态

- 尝试运行活跃 route-only 评测时，本机 API 已不可达；评测在建立连接阶段退出，没有发出模型请求或生成样本。
- 按项目入口重启 API 后，服务启动成功，但 `/model/configuration` 显示模型未配置。旧进程中的 API Key 不会随进程重启恢复；未读取或记录密钥，也没有继续发送模型请求。
- 隔离 Runtime 评测脚本原先引用了已移除的 `LangChainSkillSelector` 和旧版 LangChain 适配器；现已迁移到当前 `ModelProviderSkillSelector` / `ModelProvider` 装配。`py_compile`、Ruff 和 `--static-only` 工具边界探针通过。
- 首次 `plan-001` 重跑又暴露了间接导入：Runtime 脚本从旧 selector-only 脚本导入 fixture writer。现已将只读 YAML fixture 构造器移入 `hostpilot.evaluation.fixtures`，两个评测脚本共用；selector-only 脚本也已迁移到当前 ModelProvider 接口。新增 fixture registry 回归测试后，评测 fixture、SkillRuntime adapter、Plan fallback 和 Skill Runtime 定向测试共 **47 项通过**；脚本编译、Ruff、格式检查与 `--static-only` 边界探针均通过。
- 用户重新配置模型后，route-only 复测完成：20 条有效样例，准确率 **18/20（90%）**，当前启用 Skill 覆盖 **4/4**，工具事件 **0**。检索 Recall@3 为 **11/11**；代码审查边界用例 `clarify-002` 因顶层澄清而未进入 Skill Gate。详细指标和两个偏差见[route-only 复测记录](2026-11-14-live-routing-route-only.md)。
- 针对 `clarify-002` 增加了选择器兜底：模型返回 `direct_answer` / `clarify`、候选评分不完整、同时给 YAML Skill 高置信分时，按 `skill_routing_evaluation_failed` 转 Plan，并传递候选 Skill 与失败原因。选择器、Chat 路由和 Skill Runtime 定向测试 **48 项通过**；Ruff 检查和格式检查通过。
- API 重启并恢复模型配置后，补丁后的完整 route-only 复测仍为 **18/20（90%）**，当前 Skill 覆盖 **4/4**；`clarify-002` 本轮由模型直接正确选择代码审查 Skill，Gate 通过。一次 `office-automation-001` 分类调用失败（Trace `ValueError`）后安全澄清；另一条偏差是会议纪要请求命中办公自动化 Skill。完整分析见[route-only 复测记录](2026-11-14-live-routing-route-only.md)。
- 通过真实 Chat Runtime 对 `clarify-002` 做了定向验证：代码审查 YAML Skill 执行完成并在缺少代码/diff 时澄清，未调用工具，临时会话已删除。该验证不覆盖 Plan 失败回退或 DeepAgents 评估。
- 隔离替身验证：SkillRuntime adapter、Plan fallback 与 Skill Runtime 定向测试 **45 项通过**，覆盖 DeepAgents 执行异常及结果评估失败后的 Plan 接棒、候选和失败原因传递，以及 YAML 执行失败回退。`evaluate_stage_c_runtime.py --static-only` 通过，模型调用与真实工具副作用均为 **0**；写操作只记录到内存 stub。
- 用户完成一次真实模型 `plan-001` 隔离 Runtime 评测：路由正确进入 `plan_execute`，模型生成两版 Plan；共 3 个工具任务均以 `tool_not_available` 失败，工具 stub 调用为 0，Verifier 未运行。总 Token **11,235**，耗时 **19.767 秒**；确定性工具边界探针通过，真实文件副作用为 0。此结果证明 Plan 分解已启动，但未验证工具任务执行成功。
- 诊断报告表明两个任务均有已注册工具，但子能力选择返回 `null`。按任务文本重放旧 BM25 检索后发现，中文 Plan 描述与只有英文元数据的评测工具没有词面交集，导致候选为空。现已将 ToolDescriptor 的 `access_summary` 加入按任务工具检索和子能力选择提示，并为隔离读/写 stub 提供中文访问摘要；重放后读取任务优先召回 `read_text_file`，报告任务优先召回 `write_text_file`。
- 新增中英文混合目录的工具检索回归测试；Plan、Runtime adapter 和 Skill Runtime 定向测试共 **46 项通过**。Ruff、格式检查、Mypy、脚本编译和静态工具边界探针通过。还需重跑真实模型 `plan-001`，确认子能力选择与工具 stub 执行成功。
- 已有一次新版分流的真实模型隔离 Runtime 结果，但 Plan 工具任务全部失败，因此不能视为 Plan 执行通过。评测脚本现在会保存子任务描述和能力选择诊断；如需再次运行，使用同一命令并在本机终端通过隐藏提示输入 Key，Key 不应写入仓库或测试记录。
- 后续 `plan-001` 复测中，读取子任务已成功选择 `read_text_file` 并运行只读 stub；写入子任务选中 `write_text_file`，但参数对象为空，因此被运行时校验拒绝（`invalid_arguments`），没有发起审批、执行写入或产生真实副作用。失败后重规划的工具能力选择报 `TypeError`，当次报告没有保留异常位置。总 Token 为 17,843，耗时 36.557 秒；确定性工具边界探针通过。
- 为补齐工具参数生成，`ToolDescriptor` 新增 `input_schema`；本地文件工具、RAG 工具、远程 MCP 工具和隔离评测 stub 会声明参数结构。Plan 子能力选择提示现包含工具 Schema，并要求模型根据任务及依赖结果提供必填参数；执行仍经过现有 validator、Policy 和审批流程。新增写入工具 Schema 选择回归测试，Plan 定向测试 **19 项通过**；Ruff、`mypy src/hostpilot`、评测脚本编译和 `evaluate_stage_c_runtime.py --static-only` 均通过。评测报告现记录 `TypeError` 的有限调用栈位置，下一次真实模型复测将验证参数生成并定位重规划异常。
- 参数 Schema 更新后的真实 `plan-001` 复测中，读取任务成功；写入能力选择在 JSON 解码阶段失败，报告调用栈指向 `model_plan.py:_decode_json_object`。Runtime 随后重规划，但新工具任务的选择请求耗尽 20,000 Token 请求预算；实际记录 18,297 Token，Verifier 未运行。审批请求为 0，stub 写入为 0，真实副作用为 0；工具边界探针通过。现有解析器只接受纯 JSON 或完整 JSON Markdown fence，已扩展为可从模型说明文字中提取完整 JSON 对象，并添加回归测试。Plan 定向测试 **20 项通过**；Ruff 与 `mypy src/hostpilot/skills/model_plan.py` 通过。预算上限未调整，需再次运行 `plan-001` 验证写入选择、审批 stub 及剩余流程能否在预算内完成。
- JSON 恢复后的真实复测未再出现 JSON 解码错误，但 Planner 将“读取 README 和 ROADMAP、比较、提交写入提案”合并成一个工具任务；旧执行器一次只选一个工具，因而只读取了 README，Verifier 拒绝了不完整结果。重规划后，能力选择又耗尽了 20,000 Token 预算（已计 17,819 Token）；审批请求、写入提案和真实副作用均为 0，工具边界探针通过。用户澄清了执行模型：Plan 按业务目标拆解，任务和按任务检索的相关工具列表交给受限 ReAct 子 Agent；子 Agent 可在单个任务内多轮推理、重复调用同一工具或继续选用 allowlist 中的其他工具。此前把每个任务限制为一次工具调用的规则不符合目标，已撤回。当前已改造 `GovernedPlanTaskExecutor` 为受限 ReAct 循环：每任务默认最多五轮（可由 Harness 调整），工具调用和模型调用共享请求预算；工具列表按目标从获准目录检索默认最多五个候选（也可配置），每次实际调用仍经治理 Provider。评测报告新增每任务候选列表、轮数和调用结果。架构决策、目标架构与路线图已同步该边界。定向测试覆盖同一任务内两次读取、使用依赖前序结果的后续决策并再次调用写工具，相关 Plan/Orchestrator 定向测试 **37 项通过**；Ruff、`mypy src/hostpilot`、评测脚本编译和 `--static-only` 工具边界探针均通过。仍需真实模型 `plan-001` 复测以验证质量与预算表现。
- Harness 配置现支持运行时调整 Plan 最大子任务数（默认 **10**，范围 1–100）、每个子任务 ReAct 轮数（默认 **5**，范围 1–32）和按任务检索工具数（默认 **5**，范围 1–20），并支持配置路由业务 Skill 高置信门槛、最小置信差、进入 Plan 的最低候选置信度以及业务 Skill/Plan 候选数量。配置通过现有 SQLite Harness API 持久化，保存后更新默认运行时中的 selector、Plan Orchestrator 和 Plan task executor；前端“Harness 配置”已增加对应输入项。后端定向测试 **121 项通过**，前端 Harness 测试 **17 项通过**，前端类型检查通过。

架构决策见 [ADR-0029](../decisions/0029-three-skill-pipelines.md)。
