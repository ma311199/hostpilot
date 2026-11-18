# Stage E：Skill 包详情只读查看验证

- 日期：2026-11-07
- 范围：按 Skill ID 查询已校验 Skill 包的文本文件，并在 Vue 页面查看
- 新增运行依赖：无

## 实现

- 新增 `GET /api/v1/skills/detail?skill_id=...`，目录 ID 匹配后再由现有 Skill 注册表重新发现并校验包；接口不接收本地路径，未知或失效的 Skill 返回通用 404。
- `SKILL.md` 包返回说明文件和注册表允许的文本资源；YAML 包返回规范化的 `skill.yaml` manifest、`SKILL.md`（若存在）及已校验文本资源。
- Vue Skill 卡片增加查看/收起文件详情交互，显示相对路径和纯文本内容；未使用 HTML 注入或执行包内容。
- 不返回源目录路径、二进制文件或校验异常文本。

## 验证

- 后端 API 定向测试：**29 passed**；后端全量测试：**232 passed**。
- 前端 Vitest：**27 passed**。
- Ruff lint/格式检查、ESLint、`vue-tsc` 类型检查和 Vite production build：通过。
- 测试期间有来自锁定 FastMCP 依赖的 Authlib 弃用警告，未影响结果；未采集覆盖率。

## 当前边界

- 详情查看只读；导入、删除、启停和 Runtime 选择仍未实现。
- YAML manifest 以校验后的结构重新序列化展示，保留契约数据但不保留原始 YAML 排版和注释。
