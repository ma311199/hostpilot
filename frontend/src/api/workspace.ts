export interface WorkspaceStatus {
  path: string
  read_only: boolean
  selected: boolean
  message: string | null
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
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }
  if (!response.ok) {
    let detail = `工作区请求失败（HTTP ${response.status}）。`
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

export function getWorkspace(): Promise<WorkspaceStatus> {
  return request<WorkspaceStatus>('/workspace')
}

export function selectWorkspace(): Promise<WorkspaceStatus> {
  return request<WorkspaceStatus>('/workspace/select', { method: 'POST' })
}
