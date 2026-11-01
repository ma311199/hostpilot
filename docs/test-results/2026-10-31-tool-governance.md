# 工具治理首版验证记录

- 日期：2026-10-31
- 范围：Tool Registry、Policy Engine、Tool Executor、Task Verifier

## 实现结果

- 工具按唯一名称登记；登记项包含参数校验器、异步执行器和任务结果校验器。
- capability 未全部获批时默认拒绝；不可信工具不能在宿主或普通服务环境执行；尚未实现的隔离环境明确拒绝。
- 高影响操作必须配置不暴露原始参数的确认摘要，并通过异步确认 Provider 获得批准；无 Provider、拒绝、取消、超时或 Provider 出错均不执行工具。
- 工具执行前预留工具/检索调用预算；执行受工具超时与请求剩余时间共同限制。
- 允许、拒绝、确认、执行失败和结果校验失败写入结构化 AuditEvent，不写入工具原始参数。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 安全与边界测试 | `.venv/bin/pytest -q tests/unit/tools tests/unit/policy tests/unit/budget` | 19 passed |
| Ruff lint | `.venv/bin/ruff check src/hostpilot/tools src/hostpilot/policy src/hostpilot/verification tests/unit/tools` | 通过 |
| Ruff 格式 | `.venv/bin/ruff format --check src/hostpilot tests` | 61 个文件格式正确 |
| 类型检查 | `.venv/bin/mypy src/hostpilot` | 44 个源文件无问题 |
| 完整测试套件 | `.venv/bin/pytest -q` | 88 passed（含后续 API、记忆审批和输出边界测试） |
| 依赖锁一致性 | `uv lock --check` | 解析 117 个包 |

本次未采集覆盖率，因此不报告覆盖率百分比。

## 已知缺口

- 尚未将 Tool Executor 接入 DeepAgents 工具调用循环；当前为可独立测试的 HostPilot 执行边界。
- 确认 Provider 接口已定义，HTTP 待审批项持久化、用户确认 API 与超时恢复流程仍未实现。
- 需要在工具进入实际调用链前补充每个工具的数据范围规则、参数模式和具体任务验证器。
