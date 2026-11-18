# 本地 Skill 开发指南

> **两种格式均为正式支持的执行形式。** DeepAgents 执行 `SKILL.md`，Skill Runtime 执行 `skill.yaml`。默认根据包内容选择：存在 `skill.yaml` 时走 YAML；契约无效则禁用该包，不回退执行 Markdown；只有 `SKILL.md` 时使用 DeepAgents。未来 Skill 管理页还会允许明确选择 YAML、DeepAgents 或两者，详见 [ADR-0013](decisions/0013-dual-skill-formats-and-execution-selection.md)。

> **2026-11-08 路由约定：**`direct_answer` 负责普通单轮、无工具问答；`orchestrator_plan` 负责复杂或需要工具/多步执行的请求；独立顶层 ReAct 工具循环已移除，Plan 工具任务内部由受限 ReAct 子 Agent 按任务目标循环调用已检索工具。三个内置 Skill 的契约见 [ADR-0029](decisions/0029-three-skill-pipelines.md)。

## 当前代码实现：DeepAgents Markdown Skill

Skill 存放在用户目录 `~/.hostpilot/skills/<skill-name>/SKILL.md`。也可以通过环境变量 `HOSTPILOT_SKILLS_DIR` 指定根目录。名称使用小写字母、数字和连字符，且必须与目录名一致。

~~~text
~/.hostpilot/skills/
└── meeting-notes/
    ├── SKILL.md
    └── reference.md
~~~

SKILL.md 使用 DeepAgents 的 YAML frontmatter 格式：

~~~markdown
---
name: meeting-notes
description: Turn rough notes into clear minutes.
---

# Instructions

Summarize decisions, owners, and due dates.
~~~

每次目录发现或 Skill 加载都会重新校验文件；编辑后无需重启应用。允许 UTF-8 的 Markdown、TXT、JSON、TOML、YAML、CSV，以及 `.py`、`.js`、`.sh` 脚本资源。脚本只作为文本资源读取，不会因导入、发现或加载而执行；Agent 需要运行脚本时，必须将源码交给对应的 `run_python_code`、`run_javascript_code` 或 `run_shell_script` 沙箱工具，仍受工具审批和沙箱策略约束。当前脚本工具接收源码字符串，不挂载 Skill 包目录；依赖同目录资源的脚本需要把依赖内容一并传入或调整为自包含脚本。符号链接、超限文件、二进制可执行文件和不支持的扩展名会被拒绝。

## 从 ZIP 导入

Skill 库的“导入 ZIP”接受一个 `.zip` 包，ZIP 根目录可直接放包文件，也可在单一顶层目录中放包文件。压缩文件最大 4 MiB，包内最多 100 个文件、解压总量最大 2 MiB。压缩包必须包含且只能识别为一个有效 `skill.yaml` 包或 `SKILL.md` 包；导入前复用对应注册表完整校验，只有校验通过才安装到 `HOSTPILOT_SKILLS_DIR` 指向的用户目录（未设置时为 `~/.hostpilot/skills`）。

导入会拒绝路径穿越、符号链接、隐藏路径、重复路径、加密 ZIP、二进制可执行文件、非 UTF-8 资源、不支持的扩展名、无效包和已存在的 Skill 名称/ID。`.py`、`.js`、`.sh` 会以普通文本资源形式保留，导入时不会执行。运行时从用户目录动态发现 Skill，成功导入后可在目录和后续请求中使用。

Skill 库允许停用或删除用户包，随包内置 Skill 不能从页面修改。YAML 包通过 `meta.enabled` 控制启用状态；更新时后端会重写为规范化 YAML，因此原始注释和排版不会保留。Markdown 包使用 `.hostpilot-disabled` 隐藏标记，停用后不会注入 DeepAgents 的 Skill 快照。删除操作需在页面确认，且不会删除用户 Skill 根目录以外的内容。

## 权限和高影响操作

Skill 指令不能自行授权工具。工具调用仍由 HostPilot Policy Engine 判断。对高影响工具，策略允许后还必须取得用户明确确认；拒绝、取消或超时均不执行。用户确认不能覆盖策略拒绝。

确认请求只应展示必要的操作摘要，不应把密钥或完整敏感参数放入用户界面或审计事件。

## DeepAgents 集成

HostPilot 先校验本地 Skill，再将内容复制为当前 Agent 图状态中的虚拟文件，供 DeepAgents 原生 SkillsMiddleware 按需读取。Agent 使用状态后端；对话中对虚拟 Skill 文件的写入不会保存回用户磁盘目录。用户通过本地文件或 Gradio 原型编辑器修改源文件。

## 本地 Gradio 原型

在已安装 prototype 依赖组的环境中，可通过 `create_skills_demo(SkillRegistry())` 创建本地目录浏览与编辑界面。明确调用 `launch_skills_demo(...)` 才会启动服务；服务绑定到 127.0.0.1，不开放公网分享。

## YAML Skill：Skill Runtime 执行契约

YAML Skill 包以 `skill.yaml` 作为机器可读的执行契约；目录可以同时包含 `SKILL.md` 作为 DeepAgents 指令或展示说明。该形式与上面的 DeepAgents Markdown Skill 并行存在，不要求旧包迁移。目录形态示例：

```text
skills/business/code_review/
├── skill.yaml
├── SKILL.md
└── assets/                 # 可选的受限静态资源
```

默认 Chat Runtime 会同时发现应用随包发布的 YAML Skill 和 `HOSTPILOT_SKILLS_DIR`（或默认 `~/.hostpilot/skills`）下的用户 Skill。应用内置的业务包位于 `src/hostpilot/skill_packages/`，不会覆盖用户包；同一注册表出现重复 `skill_id` 时，重复项都会被拒绝并报告问题。

`skill.yaml` 使用 schema version 1，未知字段会被拒绝。用户 Skill ID 使用 `business/<id>` 或 `community/<id>`；`builtin/` 由 HostPilot 保留。最小契约如下：

```yaml
schema_version: 1
meta:
  skill_id: business/meeting-notes
  name: Meeting notes
  version: 1.0.0
  priority: 50
  enabled: true
intent_summary: Turn rough meeting notes into minutes and action items.
tags: [meeting notes, minutes]
examples:
  - summarize these meeting notes
  - make meeting minutes
input_schema:
  type: object
  properties:
    notes: { type: string }
  required: [notes]
  additionalProperties: false
output_schema:
  type: object
  properties:
    minutes: { type: string }
  required: [minutes]
  additionalProperties: false
side_effect_level: read_only
execution:
  kind: steps
  steps:
    - step_id: summarize
      action: llm.complete
      depends_on: []
      params: {}
    - step_id: extract_actions
      action: actions.extract
      depends_on: []
      params: {}
    - step_id: combine
      action: result.combine
      depends_on: [summarize, extract_actions]
      params: {}
dependencies:
  skills: []
  tools: []
```

`input_schema` and `output_schema` use JSON Schema Draft 2020-12. An execution is either `kind: builtin` with `skill_type`, or `kind: steps` with unique `step_id`, `action`, optional `params`, and optional `depends_on`. `YamlSkillRegistry` parses and validates the package; Stage C Runtime compiles the validated dependencies into a LangGraph DAG and validates runtime inputs/outputs. Independent nodes can run in parallel, and a node with multiple dependencies waits for all of them. Unknown dependencies, self-dependencies, duplicate dependencies, and cycles are rejected. For backward compatibility, omitted `depends_on` means “depends on the previous step”; explicit `depends_on: []` means a root step that may run alongside other roots. Without LangGraph, Runtime uses a sequential adapter that preserves dependency correctness.

`dependencies.skills` serves two linked purposes: the referenced Skill must be available during package preflight, and it is the explicit child Skill allowlist when this business Skill falls back to Plan. An empty list means the Skill cannot delegate to another business Skill. The top-level builtin `orchestrator_plan` may select from all enabled, validated business Skills; a Plan created as fallback for a business Skill sees only that parent's declared list. Runtime validates the final Plan again before executing any child task.

`dependencies.tools` is the corresponding tool allowlist. List each stable MCP tool ID used by a YAML step action or allowed to a business Skill's Plan fallback. Package preflight rejects unknown tool dependencies and registered MCP actions omitted from the list. Runtime checks step actions before admission or execution. During Plan fallback, the Planner sees an abstract tool-discovery capability rather than the full tool catalogue; Task Executor later retrieves only tools that are both declared by the parent and currently registered. An empty list means no tool tasks. The final Plan is checked again before task execution. The top-level builtin `orchestrator_plan` can use the currently registered tool catalogue at execution time. These declarations constrain capability selection; actual calls still pass through the Tool Executor and Policy Engine, which validate parameters, budget, risk, and confirmation.

Remote MCP tool arguments are also checked against the Server's discovered `inputSchema` by the Tool Executor before policy evaluation, user confirmation, or invocation. Schemas use JSON Schema Draft 2020-12, are limited to 512 nodes and 16 nested levels, and may use valid local JSON Pointer references only. External references, unresolved pointers, invalid schemas, and oversized schemas fail closed; an invalid schema prevents that Server's tools from being registered. Tool-specific local validators remain authoritative for built-in tools.

If the DAG has multiple terminal steps and no explicit combine step, their results are returned as an object keyed by `step_id`; a single terminal step returns its output directly. `input_mapping` references use `steps.<step_id>.<path>` and must point to an ancestor step in the dependency graph so parallel scheduling cannot race a read.

`side_effect_level` is required and accepts `read_only`, `idempotent`, `reversible`, or `irreversible`. The Runtime uses it to decide whether a failed post-execution assessment may be handed to Plan automatically. Reversible operations require a verified compensation path; irreversible operations are not automatically retried. This declaration does not authorize a tool call.

Runtime checks required input fields before the first workflow action and returns a clarification prompt when a required field is absent. A present field with an invalid type or structure fails JSON Schema validation; it is not silently converted into a clarification. Admission and result assessors may also return `CLARIFY`, `PLAN`, or `REJECT`. Skill and tool allowlists are system admission checks, not interactive grants. A high-impact tool requests user confirmation only after PolicyEngine allows the call; a missing capability or missing confirmation provider fails closed. There is no generic confirmation dialog for every non-read-only Skill.

Built-in packages use `meta.skill_id` values `builtin/direct_answer`, `builtin/clarify`, and `builtin/orchestrator_plan`, and bind an Executor through `execution.kind: builtin` and `execution.skill_type`. User packages cannot declare the reserved `builtin/` namespace. Business/community packages use declarative steps. A Skill may reference a sub Skill or stable MCP tool ID but cannot specify a transport, endpoint, credential, or tool permission grant. Sub Skill candidates are limited by the parent Skill's `dependencies.skills` list as described above.

默认 Runtime 注册了只读 `llm.complete` step action。它通过当前已配置模型处理 `input_mapping` 提供的 `source` 文本，执行 `params.instruction` 中的任务说明，不带工具调用能力；提供方返回的 Token 用量纳入请求预算和聊天用量。模型输入按不可信材料处理。

### 内置业务 Skill：代码审查

应用附带 `business/code-review-check`，用于审查用户直接粘贴的代码或 diff。它只分析输入文本，输出有依据的严重级别、位置、影响和修复建议；不读取仓库、不运行代码/测试、不调用工具，也不修改文件。输入缺失或为空会在模型调用前被 JSON Schema 拒绝。该 Skill 的路由与执行仍经过顶层选择器、准入评估、Runtime 和结果评估。

YAML Skill 路由以 YAML 结构化元数据作为权威匹配数据；DeepAgents Markdown Skill 则由 DeepAgents SkillsMiddleware 使用经过校验的指令。两套入口分别路由和执行，不把自由文本解释成 YAML steps。当前 `SkillSignatureIndex` 预建 YAML examples、tags 和 intent_summary 索引，`MetadataSkillRouter` 对其执行保守、可复现的匹配；证据不足时不返回 YAML Skill。它不做语义向量匹配，也不授权工具调用。用户提供的 YAML、Markdown 和资源仍按不可信输入处理：校验 schema、包路径、资源大小和依赖；Skill 声明的工具仍须经 HostPilot 准入策略。

**当前实现：**`SkillRegistry` 供 DeepAgents Runtime 加载 Markdown Skill；`YamlSkillRegistry` 发现和校验 YAML 契约包，默认 Chat API 使用统一选择器和 Skill Runtime。统一目录合并用户包与只读 bundled 包；双格式包默认由 YAML Runtime 执行，也可在 Web 管理页选择 YAML、DeepAgents 或 `both`。选择偏好写入 SQLite 并即时作用于分类器、YAML Runtime 和 DeepAgents 适配器；`both` 每次请求只走一个已选入口。没有有效选择结果时的 Chat Runtime 回退行为不等同于逐 Skill 模式配置。已注册 MCP 工具可作为 YAML action 调用，沿用 Tool Executor 的策略、审批、预算和验证；Plan 的 `business_skill_task` 可通过 Skill Runtime 委派已启用业务 Skill，`tool_task` 则由受限 ReAct 子 Agent 在按任务检索的工具 allowlist 内循环调用工具。模型选择结果仍是非可信提议，运行时会重新校验 Skill、工具 ID 和输入 schema。选择器模型不可用或结构化结果无法解析时进入 `clarify`；若分类结果与高置信业务候选冲突，或存在多个超过 Plan 候选阈值的业务 Skill，则转交 Plan 并携带失败原因。Runtime 契约见 [ADR-0009](decisions/0009-skill-runtime-and-yaml-contract.md)，注册表规则见 [ADR-0011](decisions/0011-yaml-skill-registry-and-legacy-migration.md)，双格式决策见 [ADR-0013](decisions/0013-dual-skill-formats-and-execution-selection.md)。

业务 Skill 快速路径和 Plan 兜底策略见 [ADR-0012](decisions/0012-business-skill-fast-path-and-plan-fallback.md)。Runtime 提供 `direct_answer` 单轮 LLM 执行器、`clarify` 执行器、模型化准入/结果语义评估、输入/输出 Schema 校验、执行快照及副作用安全门。分类、Planner、Verifier 和 Skill 评估器通过普通 Chat 调用请求 JSON 文本，由 HostPilot 本地解析并执行 Pydantic/schema 校验，不依赖供应商的 JSON Schema response_format 或 function calling 支持；无效 JSON 按对应的 fail-closed 路径处理。confidence 低于唯一高置信门槛，或存在多个超过 Plan 候选阈值的业务 Skill 时转入 Plan；评估调用受请求预算限制，usage 计入聊天 Token 用量。模型评估不能覆盖确定性的 JSON Schema、Policy Engine 或 Tool Executor 校验。`OrchestratorPlanExecutor` 校验 Plan DAG，并按依赖 wave 并行调度就绪任务，默认最多同时执行 4 项；下游任务等待依赖 wave 完成。取消协议支持宿主注册补偿 handler：只补偿具备 handle 且标记为 `reversible` 的副作用，按完成顺序逆序调用；不确定的 in-flight 副作用只有在 handler 显式声明安全时才会尝试补偿。内置文件工具中，普通 `write_text_file` 仍不可逆；`write_existing_text_file` 只覆盖既有文件，执行前以内存快照记录旧/新内容，取消时做条件恢复。恢复须重新通过 Tool Executor、预算、策略和用户审批；如果当前文件已被外部修改则失败转人工处理。内存快照不支持进程重启后恢复。远程 MCP action 没有可信的自动补偿契约，仍按不可逆处理。取消异常和持久化摘要记录补偿结果，但不会存储 handle 或文件内容。模型 Planner/Verifier、MCP 工具和子 Skill 执行器通过适配器提供。默认 Chat API 已接入顶层选择器；相关验证见[评估记录](test-results/2026-11-03-stage-c-semantic-assessment.md)、[并行调度记录](test-results/2026-11-03-stage-c-plan-parallelism.md)和[补偿实现记录](test-results/2026-11-03-stage-c-compensation-implementation.md)。
