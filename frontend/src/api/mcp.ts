export type ToolConnectionTransport = 'in_process' | 'streamable_http' | 'stdio'
export type ToolConnectionState = 'connecting' | 'connected' | 'disconnected' | 'failed'
export type MCPServerState = ToolConnectionState | 'waiting_for_credentials' | 'disabled'

export interface ToolConnectionStatus {
  connection_id: string
  transport: ToolConnectionTransport
  state: ToolConnectionState
  discovered_tool_count: number
  error_code: string | null
}

export interface ToolConnectionsResponse {
  connections: ToolConnectionStatus[]
}

export interface MCPServerConfiguration {
  name: string
  transport: 'streamable_http' | 'stdio'
  url: string | null
  command: string | null
  args: string[]
  cwd: string | null
  timeout_seconds: number
  enabled: boolean
  api_key_header: string | null
  api_key_env: string | null
  api_key_configured: boolean
  managed: boolean
  connection_state: MCPServerState
  error_code: string | null
}

export interface MCPServerConfigurationInput {
  name: string
  transport: 'streamable_http' | 'stdio'
  url: string | null
  command: string | null
  args: string[]
  cwd: string | null
  timeout_seconds: number
  enabled: boolean
  api_key_header: string | null
  api_key_env: string | null
  api_key?: string
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
    let detail = `MCP 请求失败（HTTP ${response.status}）。`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the status based error when the server did not return JSON.
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getToolConnections(): Promise<ToolConnectionsResponse> {
  return request<ToolConnectionsResponse>('/mcp/connections')
}

export function getMCPServers(): Promise<{ servers: MCPServerConfiguration[] }> {
  return request<{ servers: MCPServerConfiguration[] }>('/mcp/servers')
}

export function saveMCPServer(
  configuration: MCPServerConfigurationInput,
): Promise<{ servers: MCPServerConfiguration[] }> {
  return request<{ servers: MCPServerConfiguration[] }>('/mcp/servers', {
    method: 'PUT',
    body: JSON.stringify(configuration),
  })
}

export function deleteMCPServer(name: string): Promise<void> {
  return request<void>(`/mcp/servers/${encodeURIComponent(name)}`, { method: 'DELETE' })
}
