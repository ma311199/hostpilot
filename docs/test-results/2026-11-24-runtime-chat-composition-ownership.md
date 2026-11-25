# Runtime Chat 组合实现归属迁移验证

## 范围

- 日期：2026-11-24
- 目标：把 Model、Skill、Plan 与 Chat Runtime 的具体同步组装逻辑迁入 `orchestration.chat_runtime_composition`；Runtime 装配插件直接使用此实现。
- 兼容性：保留 `orchestration.runtime.create_configured_chat_agent_runtime()`、原函数签名及包级导出，由旧入口委托给唯一组合函数。
- Runtime Host 仍负责依赖与服务生命周期；API 仍负责候选 Provider、配置保存和多资源切换；Chat Execution 插件仍负责 Agent 代际发布及关闭。
- 未运行真实模型评测或前端测试；本次没有前端改动。覆盖率未单独量化。

## 行为覆盖

- 兼容工厂将全部参数传给新组合函数，返回其结果并原样传播异常；原函数签名/命名检查保持通过。
- Runtime 装配插件直接调用新组合模块，不再调用兼容工厂；Host Checkpointer、Trace、Cancellation，候选工具/Provider/Memory、Skill 模式及各扩展注册表均按原契约传递。
- API 初始装配、MCP 刷新和主模型刷新继续通过 Runtime 服务构建候选；失败候选关闭且不会替换活动 Provider/Runtime。
- 自定义 Chat Runtime 工厂、无主模型启动、无 ToolFacade 以及现有 Model/Skill/Plan 组合行为保持覆盖。
- 终审补强：完整锁定兼容工厂参数名、类型、参数种类、默认值、必填参数及包级导出；实际组合函数验证自定义 Model Provider、Chat Runtime 和五类 Plan 组件实现被采用；无 ToolFacade 时模型工具定义与 allowlist 为空，且不可用 Tool Port 拒绝派发。
- 组合辅助函数 `_tool_descriptors`、`_runtime_tool_definitions` 与无 Provider Tool Port 随组合实现一并迁移。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| TDD 兼容包装测试 | 实现前 **2 failed**，均因组合模块不存在；提取后通过 |
| TDD Runtime Plugin 测试 | 切换前 **1 failed**，确认仍调用旧入口；切换后装配相关 **3 passed** |
| `.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py tests/unit/test_runtime_naming.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/api/test_app.py` | **106 passed**，1 条既有 Authlib 弃用警告 |
| `.venv/bin/pytest -q tests/unit/test_runtime_naming.py tests/unit/orchestration/test_runtime.py` | **14 passed** |
| `bash scripts/check_quality.sh` | Ruff 格式检查 **235 files**、Ruff lint 通过、mypy **130 source files**、Python 全量 **760 passed**、`uv lock --check` 通过 |
| `git diff --check` | 通过 |

## 过程记录

- 首轮格式检查要求调整三个测试文件格式和两个局部导入顺序，修正后 Ruff 通过。
- 首轮 mypy 指出新组合模块从 `runtime.py` 导入未显式导出的 Plugin Context/Registry 类型；改为从 `runtime_plugins` 的定义模块导入后，全仓 mypy 通过。
- 保留 1 条既有 Authlib `authlib.jose` 弃用警告。没有运行真实模型评测，没有将通过数解释为模型质量或覆盖率。
- 工作区原有未提交 Runtime/Sandbox/Turn Recovery 改动全部保留；没有提交或推送。
- 终审没有发现生产实现缺陷；新增断言用于防止未来回归。全量检查首轮只提示一个测试文件需 Ruff 格式化，格式化后重新运行并全部通过。

## 结果与边界

- 新组合模块是默认 Runtime 装配与兼容入口共用的唯一组合实现。
- 兼容入口仍可供仓库外调用方使用；本次不移除公开符号。
- 候选构建失败后的 Provider 清理保持现有 API 责任；配置保存失败时已构建候选 Agent 的关闭缺口不属于本次范围。
