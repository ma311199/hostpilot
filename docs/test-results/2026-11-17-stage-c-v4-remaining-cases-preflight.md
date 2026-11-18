# 2026-11-17：Stage C v4 剩余用例评测预检

## 范围

- 数据集：`stage-c-model-evaluation` v4。
- 目标：从已记录的 `plan-004` 至 `plan-008`、`safety-001` 至 `safety-004` 之后，继续检查其余运行时样例并补齐 v4 指标。
- 隔离方式：`evaluate_stage_c_runtime.py` 为每例创建临时目录，治理工具使用内存 stub；报告声明真实工具副作用为 0。
- 本记录只描述评测启动预检，不是模型质量结果。

## 用例覆盖审查

v4 包含 30 条运行时路由/行为用例和 3 条故障注入用例。在 direct/clarify 批次完成、业务 Skill 批次运行前，已有带 v4 数据集标记的 Runtime 报告覆盖 `direct-001` 至 `direct-005`、`clarify-001` 至 `clarify-005`、`plan-004` 至 `plan-008` 与 `safety-001` 至 `safety-004`，合计 19/30。当时尚未运行的 11 条为：

- `skill-meeting-001` 至 `skill-meeting-004`；
- `skill-code-001` 至 `skill-code-004`；
- `plan-001` 至 `plan-003`。

早期 v1–v3 报告可作为历史诊断，但不计入 v4 覆盖。`fault-json-001` 至 `fault-json-003` 属于确定性模型输出故障注入，不由当前真实模型 Runtime 脚本执行。已有 [JSON 输出与 fail-closed 验证记录](2026-11-04-stage-c-json-model-output.md)及对应单元测试覆盖解析和拒绝行为；本轮没有把它们逐条映射到 v4 故障样例，因此继续单独维护，不混入 30 条真实模型运行时分母。

## 预检尝试

执行命令：

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v4.yaml \
  --case-id direct-001 --case-id direct-002 --case-id direct-003 \
  --case-id direct-004 --case-id direct-005 \
  --case-id clarify-001 --case-id clarify-002 --case-id clarify-003 \
  --case-id clarify-004 --case-id clarify-005 \
  --token-budget 20000 --tool-call-budget 20 \
  --output /tmp/hostpilot-stage-c-v4-direct-clarify.json
```

- 结果：在运行任何用例前退出，未生成评测报告。
- 原因：本次以非交互方式启动，脚本检测到 stdin 不是 TTY，因此不能像既定流程那样安全交互提示输入密钥。交互终端运行时可手动输入；不需要从 API 进程读取密钥，也不必把密钥写入命令或报告。
- 指标：运行样本 0；成功/失败用例数均未产生；Token、延迟、路由准确率均未测。该预检不计为模型失败，也不计入 v4 分母。
- 安全边界：没有发起模型请求或工具调用，未产生副作用。
- 后续：由用户在交互终端运行下面的 direct/clarify 批次命令并手动输入密钥；完成后继续运行 8 条业务 Skill 和 `plan-001` 至 `plan-003`。不得将密钥写入报告或聊天。

```bash
.venv/bin/python scripts/evaluate_stage_c_runtime.py \
  --dataset docs/evaluations/stage-c-model-evaluation-v4.yaml \
  --case-id direct-001 --case-id direct-002 --case-id direct-003 \
  --case-id direct-004 --case-id direct-005 \
  --case-id clarify-001 --case-id clarify-002 --case-id clarify-003 \
  --case-id clarify-004 --case-id clarify-005 \
  --token-budget 20000 \
  --tool-call-budget 20 \
  --show-response \
  --output /tmp/hostpilot-stage-c-v4-direct-clarify.json
```

## 当前 v4 运行时覆盖

按带 v4 数据集标记的独立 Runtime 报告计，30/30 条用例均已有 Runtime 报告（100%）；这表示覆盖完整，不表示全部通过。Plan 批次结果与 `plan-001` 后续诊断见[专项报告](2026-11-17-stage-c-v4-plan-runtime.md)。direct/clarify 批次的路由结果与人工质量审查见[专项报告](2026-11-17-stage-c-v4-direct-clarify-runtime.md)；业务 Skill 首轮和会议复测见[专项报告](2026-11-17-stage-c-v4-business-skill-runtime.md)。会议纪要修复后 4 条均在 40,000 Token 预算下完成；统一响应渲染器已修复 `text` 字段 JSON 包装，`skill-meeting-001` 与 `skill-code-002` 两例抽查通过（26,086 Token，P50/P95 26.668/29.183 秒，0 工具调用/副作用），终端正文无 JSON 包装。3 条故障注入另由确定性解析/校验测试覆盖；当前没有逐 v4 case 的独立复核报告。
