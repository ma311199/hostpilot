export interface TraceSummary {
  trace_id: string
  first_occurred_at: string
  last_occurred_at: string
  event_count: number
  last_event: string
  status: string | null
  skill_id: string | null
}

export interface TraceEvent {
  event_id: string
  span_id: string
  parent_span_id: string | null
  name: string
  occurred_at: string
  attributes: Record<string, string | number | boolean>
}

export interface TraceDetail {
  trace_id: string
  events: TraceEvent[]
}

const defaultApiBaseUrl = 'http://127.0.0.1:8000/api/v1'

function getApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  return (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
}

async function request<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`)
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }
  if (!response.ok) {
    if (response.status === 404) throw new Error('Trace 不存在或已过期，请刷新列表。')
    throw new Error(`读取 Trace 失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as T
}

export function getTraces(limit = 100): Promise<TraceSummary[]> {
  const query = new URLSearchParams({ limit: String(limit) })
  return request<TraceSummary[]>(`/observability/traces?${query.toString()}`)
}

export function getTrace(traceId: string): Promise<TraceDetail> {
  return request<TraceDetail>(`/observability/traces/${encodeURIComponent(traceId)}`)
}
