# 真实模型 Plan 与 YAML 预算冒烟验证

## 范围与结果

- 使用页面中配置的 OpenAI 兼容模型，通过运行中的本地 Chat API 验证生产 Runtime 路径；报告不记录 API Key、服务 URL、提示正文或模型响应正文。
- Plan 探针临时设置总预算 12,000 Token、工具调用 2 次、知识检索 0 次。请求被路由至 `plan_execute`，总用量为 3,380 Token；脱敏 Trace 显示 Plan 成功，并完成两次只读 `read_text_file` 调用。未请求写入或其他副作用。
- YAML 探针临时设置总预算 6,000 Token、工具调用 0 次、知识检索 0 次，使用内置 `business/code-review-check` 的 YAML 执行入口。请求路由为 `skill_execute`，总用量为 3,257 Token；Trace 显示 Skill 执行完成。
- 三根并行 YAML 探针临时导入三个独立的只读 `llm.complete` 根步骤，并使用当前 20,000 Token Harness 配置执行。请求路由至 `skill_execute`，Trace 显示临时 Skill 以 `yaml` 模式完成，总用量为 17,933 Token。工具和检索额度没有为此探针调整。
- 两次请求均使用独立临时会话，会话已删除。Harness 原配置已恢复并核实为 20,000 Token、5 次工具调用、2 次知识检索；模型运行时仍报告已配置。
- 三根并行 YAML 探针的临时 Skill 包和会话也已删除；探针期间没有发起工具调用或文件修改。Harness 保持原有 20,000 Token、5 次工具调用、2 次知识检索配置。

## 限制与后续

- 真实 Provider 验证现覆盖 Plan + 两次只读工具调用、单步 YAML 和三根并行 YAML 模型步骤；尚未在同一聊天请求中混合 Plan 与 YAML。
- 三根并行 YAML 探针消耗 17,933/20,000 Token 后成功完成，真实请求未触发超额熔断；Provider 超额后排队模型调用和后续任务被阻止的边界由确定性 Provider 回归测试覆盖。
