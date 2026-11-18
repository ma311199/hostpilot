export interface RoutingModelConfigurationStatus {
  configured: boolean
  saved: boolean
  judge_api_key_configured: boolean
  embedding_api_key_configured: boolean
  reranker_api_key_configured: boolean
  judge_model: string | null
  embedding_model: string | null
  reranker_model: string | null
  judge_base_url: string | null
  embedding_base_url: string | null
  reranker_base_url: string | null
}

export interface RoutingModelConfigurationInput {
  judge_model?: string | null
  embedding_model?: string | null
  reranker_model?: string | null
  judge_api_key?: string
  embedding_api_key?: string
  reranker_api_key?: string
  judge_base_url?: string | null
  embedding_base_url?: string | null
  reranker_base_url?: string | null
}

const apiBaseUrl = (
  (import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined)?.trim()
  || 'http://127.0.0.1:8000/api/v1'
).replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail || `请求失败（HTTP ${response.status}）`)
  }
  return await response.json() as T
}

export function getRoutingModelConfiguration(): Promise<RoutingModelConfigurationStatus> {
  return request('/routing-model/configuration')
}

export function saveRoutingModelConfiguration(
  configuration: RoutingModelConfigurationInput,
): Promise<RoutingModelConfigurationStatus> {
  return request('/routing-model/configuration', {
    method: 'PUT',
    body: JSON.stringify(configuration),
  })
}
