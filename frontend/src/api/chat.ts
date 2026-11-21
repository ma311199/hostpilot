export interface ConversationSummary {
  session_id: string
  title: string
  created_at: string
  updated_at: string
  sandbox_mode: SandboxMode
}

export type SandboxMode = 'read-only' | 'workspace-write' | 'danger-full-access'

export interface ConversationMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
}

interface ConversationHistory {
  conversation: ConversationSummary
  messages: ConversationMessage[]
}

export interface ChatResponse {
  session_id: string
  request_id: string
  assistant_message: string
  route: 'direct' | 'clarify' | 'tool_call' | 'plan_execute' | 'skill_execute'
  clarification_prompt: string | null
  token_usage: { input_tokens: number; output_tokens: number; total_tokens: number } | null
}

export interface PendingToolOutcome {
  tool_call_id: string
  tool_name: string
  created_at: string
  state: 'pending'
}

export type ToolOutcomeResolution =
  | { resolution: 'completed'; result_text: string }
  | { resolution: 'not_applied' }

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
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the status based message when the server did not return JSON.
    }
    throw new Error(detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>('/conversations')
}

export function createConversation(title?: string): Promise<ConversationSummary> {
  return request<ConversationSummary>('/conversations', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

export function deleteConversation(sessionId: string): Promise<void> {
  return request<void>(`/conversations/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

export function upgradeConversationSandbox(
  sessionId: string,
  targetMode: SandboxMode,
): Promise<ConversationSummary> {
  return request<ConversationSummary>(
    `/conversations/${encodeURIComponent(sessionId)}/sandbox/upgrade`,
    { method: 'POST', body: JSON.stringify({ target_mode: targetMode }) },
  )
}

export async function getConversationMessages(
  sessionId: string,
): Promise<ConversationHistory> {
  return request<ConversationHistory>(`/conversations/${encodeURIComponent(sessionId)}/messages`)
}

export function listPendingToolOutcomes(sessionId: string): Promise<PendingToolOutcome[]> {
  return request<PendingToolOutcome[]>(
    `/conversations/${encodeURIComponent(sessionId)}/tool-outcomes`,
  )
}

export function resolveToolOutcome(
  sessionId: string,
  toolCallId: string,
  resolution: ToolOutcomeResolution,
): Promise<{ resolved: boolean }> {
  return request<{ resolved: boolean }>(
    `/conversations/${encodeURIComponent(sessionId)}/tool-outcomes/${encodeURIComponent(toolCallId)}/resolve`,
    { method: 'POST', body: JSON.stringify(resolution) },
  )
}

