# Stage F：Skill JSON Schema 安全边界验证

## 改动

用户 Skill manifest 的输入和输出 JSON Schema 在包发现时执行有界预检：最多 4,096 个 Schema 节点、最大嵌套深度 32；`$ref`、`$dynamicRef` 和 `$recursiveRef` 仅接受以 `#` 开始的包内引用。外部 HTTP、文件路径和相对文件引用均在注册前拒绝，`$defs` 与本地 JSON Pointer 仍可使用。

## 验证

- 覆盖合法本地 `$ref`、远程 `$ref` 拒绝、嵌套深度上限和节点数上限。
- Registry、Skill Runtime 和 Plan orchestration 定向测试：44 项通过。
- Registry 定向覆盖率：15 项通过，`yaml_registry.py` 行覆盖率 89%。
- 后端全量测试：282 项通过，1 条第三方 Authlib 弃用警告。
- Ruff 检查和格式检查通过。
- 前端全量测试：13 个文件、51 项通过；ESLint、TypeScript 类型检查和生产构建通过。

## 边界

该规则阻止 JSON Schema 声明外部资源依赖，并限制 Schema 解析规模；它不构成本地 Skill 或工具执行沙箱。未受信任的代码/工具隔离仍按 Stage F 后续计划评估。
