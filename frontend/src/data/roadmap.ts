export type PhaseStatus = 'complete' | 'active' | 'planned'

export interface ProjectPhase {
  id: string
  name: string
  title: string
  status: PhaseStatus
  summary: string
  delivered: string[]
  next: string[]
}

export const projectPhases: ProjectPhase[] = [
  {
    id: 'phase-0',
    name: 'PHASE 0',
    title: '项目基线',
    status: 'complete',
    summary: '后端骨架、会话持久化、模型运行时和本地 Skill 已有首版。',
    delivered: ['FastAPI 与 SQLite 会话 API', 'ContextBuilder 与模型运行时', 'Markdown 记忆和 Skill 管理'],
    next: [],
  },
  {
    id: 'phase-1',
    name: 'PHASE 1',
    title: '受控主路径',
    status: 'active',
    summary: '真实模型 MCP 审批已手动联调；query-only 检索工具已支持本地凭证配置和 EvidenceSet 校验。',
    delivered: [
      '请求长度、超时和预算契约',
      'Router 边界与澄清消息持久化',
      'Tool Registry、Policy Engine 与验证模块',
      '记忆提案显式审批 API',
      'L4 响应长度和非空校验',
      'FastMCP 工具接入 Agent、授权、执行和结果验证链',
      '模型实际 Token 用量进入响应、预算和 Cost 事件',
      '真实模型发起工具调用并通过浏览器审批/拒绝的联调',
      'query-only RAG MCP 工具和后端凭证注入',
      '未配置 RAG 时返回明确占位提示',
      '工具目录页面展示后端注册项、配置状态和审批要求',
    ],
    next: [
      '使用真实 RAG 服务验证接口契约和 EvidenceSet 响应',
    ],
  },
  {
    id: 'phase-2',
    name: 'PHASE 2',
    title: '受控计划编排',
    status: 'planned',
    summary: '规划、DAG 执行、有限重规划和结果聚合。',
    delivered: [],
    next: ['Planner 与计划审查', '子 Agent 隔离执行', '验证和有限重规划'],
  },
  {
    id: 'phase-3',
    name: 'PHASE 3',
    title: '不可信代码隔离',
    status: 'planned',
    summary: '为不可信代码和第三方工具接入受策略控制的沙箱。',
    delivered: [],
    next: ['确定首个沙箱后端', '实现 capability 和资源限制', '验证超时清理与审计'],
  },
]

export const latestQualityCheck = {
  date: '2026-10-31',
  testsPassed: 117,
  summary: '后端统一质量脚本通过',
}
