# ADR-0003：本地 Skill 与高影响操作确认

- 状态：已接受
- 日期：2026-10-29

## 背景

HostPilot 的个人客户端需要允许用户扩展 Agent 能力，同时保证 Skill 指令不能自行授予工具权限或触发未经确认的高影响操作。

## 决定

- 首版支持用户在本地添加和修改 Skill。
- Skill 使用 DeepAgents 的 SKILL.md YAML frontmatter 格式，默认根目录为 ~/.hostpilot/skills，可通过 HOSTPILOT_SKILLS_DIR 覆盖；包内资源按首版支持格式校验。
- 用户编辑的 Skill 内容按不可信输入处理。注册和加载时校验包结构、文件路径边界和元数据；不得因加载 Skill 而自动执行包内代码。
- DeepAgents 适配器负责将已选 Skill 提供给 Agent；HostPilot Skill Registry 负责发现、校验和版本/内容指纹记录。
- Skill 可声明所需工具，但声明不是授权。所有工具调用仍由 Policy Engine 判定。
- 高影响副作用在执行前必须向用户展示操作并取得明确确认。拒绝或取消确认时不得执行，并记录结果。
- 用户确认与 Policy Engine 授权是两个独立条件；用户确认不能覆盖策略拒绝。

## 后果

- Skill 需要覆盖本地添加、修改、无效包拒绝、路径逃逸防护、工具权限和确认状态的测试。
- 高影响工具的具体分类由 ToolDescriptor 和策略配置逐步明确；Gradio 原型只绑定本地回环地址。
