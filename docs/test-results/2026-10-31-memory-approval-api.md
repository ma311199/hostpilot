# 记忆提案审批 API 验证记录

- 日期：2026-10-31
- 范围：MemoryWriteProposal HTTP 提交、待审批列表、明确批准/拒绝和会话删除联动

## 实现结果

- `POST /api/v1/memory-proposals` 只进入待审批队列，不写 Markdown；单个记忆目标同一时间只允许一个待审批提案。
- `GET /api/v1/memory-proposals` 返回待处理项；`POST .../{proposal_id}/approve` 显式批准后才调用 MemoryWriteService；`DELETE .../{proposal_id}` 拒绝并丢弃。
- 对话摘要提案在提交时校验会话存在；会话删除会清理该会话尚未审批的提案。
- 提案大小受 UTF-8 文件大小限制和契约字符上限限制；提案正文不会被用作授权信息。
- 提交、批准、写入完成/失败、拒绝和会话删除清理会通过 `StructuredAuditSink` 记录结构化审计元数据，不包含提案正文。
- 待审批队列存于进程内存。服务重启后待审批项会清除；审批 API 尚未接入独立 Web 前端或模型自动提案。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| API / 存储测试 | `.venv/bin/pytest -q tests/unit/memory tests/unit/api/test_app.py` | 21 passed |
| 完整测试套件 | `.venv/bin/pytest -q` | 88 passed |
| Ruff lint / 格式 | Ruff check 与 format check | 通过，64 个文件格式正确 |
| 类型检查 | `.venv/bin/mypy src/hostpilot` | 46 个源文件无问题 |
| 依赖锁一致性 | `uv lock --check` | 解析 117 个包 |
| 仓库统一质量脚本 | `bash scripts/check_quality.sh` | 全部通过：格式、lint、类型检查、88 项测试及 117 个锁定包 |

未采集覆盖率，因此不报告覆盖率百分比。
