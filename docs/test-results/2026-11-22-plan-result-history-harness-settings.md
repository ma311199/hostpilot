# Plan 历史结果 Harness 设置验证（2026-11-22）

## 范围

在 Harness 配置中管理新 Plan 的历史成功子任务上下文窗口，并将同 Session 的有界结果作为 Planner 的独立参考输入。窗口默认最近 32 条、65,536 字符，允许分别配置 1–128 条与 1–262,144 字符；配置通过 SQLite 持久化并即时用于后续 Plan。

## 行为与数据边界

- 查询只包括当前 Session、当前 Turn 之前成功完成且有结果的子任务；历史按时间正序提供给 Planner。
- 数量和字符预算共同限制返回结果；字符按序列化记录字符数计算。Unicode 字符不按 UTF-8 字节折算。
- Planner 将历史结果视为参考，不认为它们已完成当前计划；历史不会写入当前 Turn 的 Blackboard 结果，也不会进入 Task Executor 输入。
- Harness 设置只控制 Planner 可见窗口，不删除或清理 SQLite 历史记录。单条结果现有 65,536 UTF-8 字节写入上限和精确指纹缓存审批保持不变。
- Harness 页面提供有界数值输入、可访问标签和字段说明；保存沿用完整配置 API。

## 失败、修复与复测

- 全仓 Ruff 首轮发现 `model_plan.py` 两处列表推导式超出 100 字符；将表达式拆行后，全仓 Ruff lint 和本次修改的 14 个 Python 文件格式检查通过。
- 配置/API、Store/生命周期、Planner 上下文和 Harness 页面分别完成定向 TDD；相关定向结果见 SDD 账本。
- 最终整体验收发现重启时已保存的自定义窗口只更新 API/页面配置，没有更新新建的 TaskLifecycleManager。新增重启恢复断言后先失败（实际条数限制仍为默认 32，而配置为 24），再于启动加载 Harness 配置后同步运行时历史限制；回归断言通过。

## 最终验证

- Python：`.venv/bin/pytest -q` → **675 passed**，1 条既有 Authlib 弃用警告。
- Ruff：`.venv/bin/ruff check src tests` 通过；修改的 14 个 Python 文件 `ruff format --check` 通过。
- mypy：`.venv/bin/mypy src/hostpilot` → 128 个源码文件通过。
- 前端：`pnpm test` → **85 passed**（18 个文件）；`pnpm lint`、`pnpm typecheck`、`pnpm build` 均通过。
- `git diff --check` 通过。
- 重启恢复回归：`.venv/bin/pytest -q tests/unit/api/test_app.py::test_harness_configuration_is_persisted_and_applied_to_live_chat_runtime` → **1 passed**；修复后的 Python 全量测试再次通过。
- 没有运行真实模型评测；需要 API Key 的模型测试仍由用户手动运行。

## 限制

历史成功输出会占用 Planner 提示词预算，用户可通过 Harness 字符上限控制该成本。该限制不构成 SQLite 数据保留策略，也不影响精确指纹审批路径。没有新增模型质量或 Token 消耗评测，因此无法据此量化历史上下文对 Planner 成功率、延迟或模型成本的影响。
