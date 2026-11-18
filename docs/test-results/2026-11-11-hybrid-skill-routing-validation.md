# 2026-11-11：当前混合路由的 ChromaDB 持久化验证

## 范围

- 验证当前双路 Skill 路由使用的真实 ChromaDB `PersistentClient`，覆盖建库、索引读取、Skill 元数据更新、删除和新索引实例重启恢复。
- 使用本地确定性 Embedding 替身，不访问远端服务；因此本报告验证 ChromaDB 存储行为，不测远端 Embedding 质量、路由模型准确率或业务路由质量。
- 当前本机 HostPilot API 的独立路由模型状态为 `configured: false`；真实小模型质量评测待配置后执行。

## 验证

- 新增集成用例：`tests/integration/test_skill_vector_index_persistence.py`。
- 命令：`.venv/bin/pytest -q tests/integration/test_skill_vector_index_persistence.py tests/unit/routing`
- 结果：**25 passed**。
- ChromaDB 中创建集合并写入两个 Skill；使用新建索引实例模拟应用重启后，可读取持久化索引。
- 更新一个 Skill 的元数据后，索引同步更新；从当前 Skill 列表移除另一个 Skill 后，重新打开集合不再返回该 Skill。
- ChromaDB 版本：`1.5.9`。

## 质量指标与限制

- 新混合路由的候选召回率、Top-1 准确率、Gate 精确率/覆盖率、分流正确率、延迟和 Token 均**未测**，当前质量基线仍待建立。
- 确定性 Embedding 只用于覆盖 ChromaDB 客户端与磁盘持久化路径，不代表生产 Embedding 模型的检索效果。
- 需在统一模型配置页面配置模型服务后，使用当前方案的固定脱敏评测集完成远端质量评测和 Chat API 端到端联调。
