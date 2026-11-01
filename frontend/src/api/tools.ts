export interface ToolCatalogItem {
  name: string
  description: string
  tool_type: 'retrieval' | 'action' | 'code'
  configured: boolean
  high_impact: boolean
  confirmation_required: boolean
  status_message: string
}

export interface ToolCatalog {
  runtime_configured: boolean
  tools: ToolCatalogItem[]
}

const defaultApiBaseUrl = 'http://127.0.0.1:8000/api/v1'

export async function getToolCatalog(): Promise<ToolCatalog> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/tools`)
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (!response.ok) {
    throw new Error(`读取工具目录失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as ToolCatalog
}
