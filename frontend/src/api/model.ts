export interface ModelConfigurationStatus {
  configured: boolean
  model_name: string | null
  base_url: string | null
}

export interface ModelConfigurationInput {
  model_name: string
  api_key: string
  base_url: string | null
}

const defaultApiBaseUrl = 'http://127.0.0.1:8000/api/v1'

function getApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  return (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new Error('无法连接后端，请检查服务和 HOSTPILOT_WEB_ORIGINS 配置。')
  }

  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the status based message when the server did not return JSON.
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

export function getModelConfiguration(): Promise<ModelConfigurationStatus> {
  return request<ModelConfigurationStatus>('/model/configuration')
}

export function saveModelConfiguration(
  configuration: ModelConfigurationInput,
): Promise<ModelConfigurationStatus> {
  return request<ModelConfigurationStatus>('/model/configuration', {
    method: 'PUT',
    body: JSON.stringify(configuration),
  })
}
