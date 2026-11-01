export interface ConversationSummary {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
}

interface ConversationHistory {
  conversation: ConversationSummary
  messages: ConversationMessage[]
}

export interface ChatResponse {
  conversation_id: string
  request_id: string
  assistant_message: string
  route: 'direct' | 'clarify' | 'tool_call' | 'plan_execute'
  clarification_prompt: string | null
  token_usage: { input_tokens: number; output_tokens: number; total_tokens: number } | null
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

export async function getConversationMessages(
  conversationId: string,
): Promise<ConversationHistory> {
  return request<ConversationHistory>(`/conversations/${encodeURIComponent(conversationId)}/messages`)
}

