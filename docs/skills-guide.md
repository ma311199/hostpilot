# 本地 Skill 开发指南

## 目录与格式

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

每次目录发现或 Skill 加载都会重新校验文件；编辑后无需重启应用。首版允许 UTF-8 的 Markdown、TXT、JSON、TOML、YAML 和 CSV 资源。符号链接、超限文件及可执行代码资源会被拒绝。包内文件只作为 Agent 可读取的数据，不会因加载而执行。

## 权限和高影响操作

Skill 指令不能自行授权工具。工具调用仍由 HostPilot Policy Engine 判断。对高影响工具，策略允许后还必须取得用户明确确认；拒绝、取消或超时均不执行。用户确认不能覆盖策略拒绝。

确认请求只应展示必要的操作摘要，不应把密钥或完整敏感参数放入用户界面或审计事件。

## DeepAgents 集成

HostPilot 先校验本地 Skill，再将内容复制为当前 Agent 图状态中的虚拟文件，供 DeepAgents 原生 SkillsMiddleware 按需读取。Agent 使用状态后端；对话中对虚拟 Skill 文件的写入不会保存回用户磁盘目录。用户通过本地文件或 Gradio 原型编辑器修改源文件。

## 本地 Gradio 原型

在已安装 prototype 依赖组的环境中，可通过 `create_skills_demo(SkillRegistry())` 创建本地目录浏览与编辑界面。明确调用 `launch_skills_demo(...)` 才会启动服务；服务绑定到 127.0.0.1，不开放公网分享。
