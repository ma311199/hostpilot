# Runtime Chat 真实模型迁移评测

- 日期：2026-11-20
- 目标插件：`builtin.runtime-chat`（显式选中；未更改默认值）
- 评测方式：真实模型 + Runtime-native Model/Tool 循环 + 进程内 ToolExecutor + 临时 SQLite checkpoint。
- 真实工具副作用：0。工具只返回内存中的 `about.txt`、`status.txt` 固定 fixture，不读取磁盘；路径校验只允许这两个 fixture。
- 真实模型实测：gpt-6-luna，streaming=true 的 4 个用例均通过，共 19,636 Token；glm-5.3 完成 `skill-code-review`（6,339 Token）、`plan-two-reads`（13,198 Token）和跨进程恢复（946 Token），均通过各自评测断言。所有评测工具调用均为内存 fixture，真实副作用为 0。

## 用例与判定

| 用例 | 覆盖 | 通过条件 |
| --- | --- | --- |
| `direct-answer` | Runtime Chat 不调用工具，直接回答简单算术问题 | 响应包含 `96` 且工具调用数为 0 |
| `read-tool` | 模型提出工具调用，经 Runtime allowlist、ToolFacade、ToolExecutor 后读取 fixture | 恰好一次 `read_text_file(path="about.txt")`，回复包含 `HostPilot` |
| `skill-clarify` | 通过 Chat Runtime Adapter 和真实 Skill Selector 执行代码审查 Skill | 路由为 `clarify`，选择 `business/code-review-check`，追问代码或 diff |
| `skill-code-review` | 通过同一 Adapter/Selector 路径执行有输入材料的代码审查 | 路由为 `skill_execute`，选择 `business/code-review-check`，报告 SQL injection |
| `recovery-after-tool` | 真实模型提出并完成读取后，注入一次后续模型失败，再用同一 session 重试 | 初次失败被准确注入，重试成功；读取 handler 总执行次数仍为 1 |
| `plan-two-reads` | Runtime Chat 路由至内置 Plan，顺序读取两个内存 fixture 并比较 | 路由 `plan_execute`、Skill `builtin/orchestrator_plan`；依次读取 `about.txt` 和 `status.txt`，响应满足匹配断言 |
| `recovery-after-process-restart` | 工具结果写入 SQLite 后退出一个 Python worker，在新 worker 重建 Runtime 并恢复同一 session | 不同进程 ID、同一 session ID、checkpoint 含已保存工具结果、最终回答成功，两个 worker 合计 handler 恰好执行 1 次 |

## 真实模型结果

| 模型 | 用例 | 结果 | Token | 工具调用 | 延迟 |
| --- | --- | --- | ---: | ---: | ---: |
| gpt-6-luna | `direct-answer` | 通过，回答 `96` | 2,566 | 0 | 2.842 秒 |
| gpt-6-luna | `read-tool` | 通过，调用 `read_text_file(path="about.txt")` 并回答“项目名称是 HostPilot。” | 5,225 | 1 | 6.428 秒 |
| gpt-6-luna | `skill-clarify` | 通过，路由 `clarify`，Skill `business/code-review-check`，追问代码或 diff | 3,867 | 0 | 8.277 秒 |
| gpt-6-luna | `recovery-after-tool` | 通过，注入模型失败后同一 session 重试并回答“项目名称：HostPilot” | 7,978 | 1（handler 执行 1 次） | 7.703 秒 |
| gpt-6-luna 小组合 | 前四例 | **4/4 通过** | **19,636** | **2** | P50 7.066 秒、P95 8.277 秒（N=4；P95 为最高观测值） |
| glm-5.3 | `skill-code-review` | 通过，路由 `skill_execute` 并指出 SQL injection，建议参数化查询 | 6,339 | 0 | 36.604 秒 |
| glm-5.3 | `plan-two-reads` | 通过；Plan 按顺序读取两个 fixture 并给出两点比较 | 13,198 | 2 | 81.937 秒 |

gpt-6-luna 组使用 streaming=true，每例预算上限 20,000 Token、8 次工具调用；报告位于 `/tmp/hostpilot-runtime-chat-live-rerun.json`、`/tmp/hostpilot-runtime-chat-skill-clarify.json` 和 `/tmp/hostpilot-runtime-chat-recovery.json`。glm-5.3 的 `skill-code-review` 与 `plan-two-reads` 报告分别位于 `/tmp/hostpilot-runtime-chat-skill-code-review.json` 和 `/tmp/hostpilot-runtime-chat-plan-two-reads.json`。Plan 用例预算为 40,000 Token、8 次工具调用。所有工具结果来自内存 fixture，真实工具副作用为 0；不同模型和不同用例的延迟、Token 不合并比较。

人工复核 `skill-code-review`：SQL 拼接的注入风险及参数化修复建议有代码依据；本次回复还指出普通的 `Alice` 输入会被拼成未加引号的 SQL 值，可能成为无效语法或列名引用，这也符合片段本身。回复将注入风险标为 Critical，但片段未说明数据库、调用权限和影响范围，具体严重级别应结合业务上下文判定；堆叠语句等影响也取决于驱动配置。上述内容不影响本例的路由和 Skill 执行结果。

两例独立使用 UUID session、请求预算和 checkpoint。报告记录插件、模型名、逐例响应匹配、工具参数、Token、工具预算和延迟；`--show-response` 只在终端输出模型正文，不写入 JSON 报告。

## 手动运行

```bash
.venv/bin/python scripts/evaluate_runtime_chat.py \
  --case-id plan-two-reads \
  --token-budget 40000 \
  --tool-call-budget 8 \
  --show-response \
  --output /tmp/hostpilot-runtime-chat-plan-two-reads.json
```

在交互终端运行。脚本优先读取 `HOSTPILOT_MODEL_*` 配置，或运行中的本地应用公开的非密钥模型设置；API Key 会通过隐藏输入提示手动输入，不会从应用 API 读取，也不会写进报告。可以用 `--case-id` 单独复测某例。运行后请检查 `passed`、响应正文、工具参数、Token、延迟和 `real_tool_side_effects=0`，再将真实结果及失败原因补记于本报告和指标台账。

`skill-code-review` 已由 glm-5.3 运行并通过；隔离执行只调用 `llm.complete`，没有工具或文件副作用。`plan-two-reads` 于 2026-11-21 由 glm-5.3 运行并通过：实际路由为 `plan_execute`，选择 `builtin/orchestrator_plan`，按序调用 `read_text_file` 两次读取 `about.txt`、`status.txt`；13,198 Token、81.937 秒、2 次工具调用，真实副作用为 0。运行输出显示多步读文件任务的 Skill 能力评估未通过（`requires_multi_step_tool_execution_read_files`），随后转交 Plan；因此该结果证明 Runtime Chat 到 Plan 的集成路径完成了此任务，不表示 Skill 快路径通过，也不表示 Plan 执行器已迁移为 Runtime-native。

跨进程恢复用例于 2026-11-21 由 glm-5.3 运行并通过：prepare worker（PID 889650）完成一次 `read_text_file(path="about.txt")`，工具结果已写入 checkpoint 后注入 `evaluation_injected_model_failure_after_tool` 并退出；resume worker（PID 889690）在同一 `session_id` 和重开的 SQLite checkpoint 中读到已保存结果，回答项目名为 HostPilot，handler 执行数为 0。两阶段合计 946 Token、10.306 秒、1 次工具调用，`fresh_process_boundary=true`、`same_session_id=true`、`total_handler_runs=1`，真实副作用为 0。父进程隐藏输入密钥后只通过子进程环境传递；两个 worker 使用临时目录中的同一个 SQLite checkpoint 和 `session_id`，结束后临时目录自动清理。评测模拟独立 Python 进程正常退出/重建，不模拟操作系统强杀、断电，亦不覆盖副作用已发生但工具结果尚未 checkpoint 的未知窗口。

```bash
.venv/bin/python scripts/evaluate_runtime_chat.py \
  --case-id recovery-after-process-restart \
  --token-budget 20000 \
  --tool-call-budget 8 \
  --show-response \
  --output /tmp/hostpilot-runtime-chat-process-recovery.json
```

预算是每个 worker 的上限。请检查报告中的 `fresh_process_boundary`、`same_session_id`、两阶段 `checkpoint_has_tool_result`、`total_handler_runs=1`、最终响应、总 Token、延迟及 `real_tool_side_effects=0`。

模型调用失败后的恢复演练：

```bash
.venv/bin/python scripts/evaluate_runtime_chat.py \
  --case-id recovery-after-tool \
  --token-budget 20000 \
  --tool-call-budget 8 \
  --show-response \
  --output /tmp/hostpilot-runtime-chat-recovery.json
```

此例要求真实模型先提出读取调用。工具成功并写入 checkpoint 后，脚本在下一次模型调用前注入一次可识别的本地故障；随后同一 `session_id` 重试原请求。报告将区分注入是否发生、第一次失败时和重试后的 handler 次数、重试错误及最终响应。若模型未先调用工具，演练按未满足前置条件失败，不会误报通过。这个故障注入验证 Runtime 的已知工具结果重放保护，不模拟 Provider 的网络层行为或进程崩溃。实测通过：第一次模型调用错误为 `evaluation_injected_model_failure_after_tool`；第一次失败前与重试后 handler 均为 1；总计 7,978 Token、7.703 秒，最终答复符合预期。

## 已完成的自动化验证

- 评测工具桩读取固定内存 fixture：定向测试 **2 passed**；全量 pytest **556 passed**，1 条既有 Authlib `authlib.jose` 弃用警告。
- 非 fixture 路径在 ToolExecutor 参数校验阶段被拒绝，handler 不会读取宿主文件。
- Ruff、脚本启动帮助及 `git diff --check` 通过。
- 脚本 mypy 检查通过。首轮发现 ToolExecutor 回调协议、SecretStr 与插件适配器视图的类型标注问题，已按现有接口收窄/转换并复查通过。
- 增加 `skill-clarify` 适配器路径后，Runtime Chat、Skill Adapter、Plugin 和评测工具定向回归 **29 passed**；加入恢复故障注入 helper 后相关定向回归 **11 passed**。加入跨进程恢复评测契约后，评测工具定向测试 **12 passed**，全量 pytest **566 passed**，1 条既有 Authlib 弃用警告；新增脚本 Ruff lint/format、mypy 与 `git diff --check` 通过。后续跨进程真实模型复测也已通过。
- SQLite 重开恢复和未知工具结果阻断仍只有自动化测试证据；本次真实模型恢复演练覆盖同一进程、同一 session 中“工具结果已持久化，后续模型调用失败”的已知结果重放场景。
- 2026-11-21 为“工具 handler 已执行、checkpoint 保存 Tool 结果失败”新增故障注入回归：故障前测试确认旧路径仍会调用恢复模型；修复后 Runtime 直接返回 `clarify`，恢复模型调用为 0、handler 副作用计数保持 1。Runtime Chat/执行循环定向测试 **21 passed**，全量 pytest **569 passed**（1 条既有 Authlib 弃用警告），全仓 mypy、Ruff lint/format 与 `git diff --check` 通过。该边界使用工具桩，不代表执行了真实外部副作用。详见[未知工具结果恢复保护](2026-11-21-unknown-tool-outcome-recovery.md)。

## 失败与修复记录

- 定向测试首轮因仓库未注册 pytest `asyncio` marker 而在收集阶段失败；改用同步测试函数内 `asyncio.run` 后进入测试。
- 随后两例因测试预算 fixture 未提供必填的 `retrieval_calls` 和 `timeout_seconds` 而失败；补齐字段后，工具成功读取与越界拒绝两例均通过。
- Ruff 首轮报导入顺序问题，mypy 首轮报告回调/密钥/适配器注解问题；均只涉及新增评测脚本，修复后 Ruff 与 mypy 均通过。
- 新增 `skill-code-review` 的契约测试先因用例不存在而按预期失败；补入评测 prompt、预期路由/Skill，并让 `skill-*` 统一经过 Chat Runtime Adapter 后通过。首轮格式检查提示该新增 prompt 应折叠为一行，执行 Ruff formatter 后复查通过。
- `plan-two-reads` 契约测试首轮因评测用例缺失而按预期失败；加入只读 Plan 用例及 `status.txt` 内存 fixture 后通过。新增状态 fixture 时首轮改动了 `about.txt` 的已有断言，随后恢复原 fixture 文本并保持路径 allowlist；Ruff 行宽检查也经格式调整后通过。

## 迁移判定

七个真实模型样例均按各自评测断言通过，覆盖直接回答、只读工具、Skill 澄清、有材料的 Skill 执行、已保存工具结果后的同进程重试、Runtime Chat 转交 Plan 后的两步只读任务，以及独立进程重建后的 SQLite 恢复。`plan-two-reads` 的 Skill 能力评估未通过并转 Plan；Plan 最终成功，但 Plan/Skill 内部仍复用现有执行路径，不能据此认定其已迁移为 Runtime-native。跨进程真实模型验证证明已 checkpoint 的工具结果可在新 Python 进程中复用且不重复执行；对“副作用已发生但结果未 checkpoint”的不确定窗口，新增故障注入测试证明 Runtime 不再调用模型或工具，并要求用户核实。该测试使用内存工具桩，不执行真实外部副作用。现有证据仍不足以切换默认插件：还需覆盖更广的 Plan 路径。达到既定迁移验收前，默认插件继续为 `builtin.deepagents`。
