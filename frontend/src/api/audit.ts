export interface AuditEvent {
  event_id: string
  trace_id: string | null
  action: string
  outcome: 'allowed' | 'denied' | 'completed' | 'failed' | 'cancelled'
  resource_type: string
  resource_id: string | null
  reason_code: string | null
  occurred_at: string
}

export interface AuditFilters {
  limit?: number
  offset?: number
  traceId?: string
  since?: string
  until?: string
}

const defaultApiBaseUrl = 'http://127.0.0.1:8000/api/v1'

function getApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  return (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
}

export async function getAuditEvents(filters: AuditFilters = {}): Promise<AuditEvent[]> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 100),
    offset: String(filters.offset ?? 0),
  })
  if (filters.traceId?.trim()) query.set('trace_id', filters.traceId.trim())
  if (filters.since) query.set('since', filters.since)
  if (filters.until) query.set('until', filters.until)

  let response: Response
  try {
    response = await fetch(`${getApiBaseUrl()}/observability/audit?${query.toString()}`)
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }
  if (!response.ok) {
    throw new Error(`读取 Audit 事件失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as AuditEvent[]
}

export function getAuditExportUrl(filters: Omit<AuditFilters, 'limit' | 'offset'> = {}): string {
  const query = new URLSearchParams()
  if (filters.traceId?.trim()) query.set('trace_id', filters.traceId.trim())
  if (filters.since) query.set('since', filters.since)
  if (filters.until) query.set('until', filters.until)
  const suffix = query.size ? `?${query.toString()}` : ''
  return `${getApiBaseUrl()}/observability/audit/export.csv${suffix}`
}
