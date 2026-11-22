export interface HarnessConfiguration {
  max_context_messages: number
  max_memory_context_chars: number
  max_request_chars: number
  max_response_chars: number
  request_token_budget: number
  request_tool_call_budget: number
  request_retrieval_call_budget: number
  request_timeout_seconds: number
  plan_max_tasks: number
  plan_tool_agent_max_turns: number
  plan_tool_candidate_limit: number
  plan_result_history_limit: number
  plan_result_history_max_chars: number
  routing_minimum_business_confidence: number
  routing_minimum_business_margin: number
  routing_minimum_plan_candidate_confidence: number
  routing_max_plan_candidates: number
  routing_max_business_candidates: number
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
    let detail = `Harness 配置请求失败（HTTP ${response.status}）。`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the status based error when the server did not return JSON.
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export function getHarnessConfiguration(): Promise<HarnessConfiguration> {
  return request<HarnessConfiguration>('/harness/configuration')
}

export function saveHarnessConfiguration(
  configuration: HarnessConfiguration,
): Promise<HarnessConfiguration> {
  return request<HarnessConfiguration>('/harness/configuration', {
    method: 'PUT',
    body: JSON.stringify(configuration),
  })
}
