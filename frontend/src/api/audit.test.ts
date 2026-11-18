import { afterEach, describe, expect, it, vi } from 'vitest'

import { getAuditEvents, getAuditExportUrl } from './audit'

describe('Audit API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('encodes supported trace and time filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await getAuditEvents({
      limit: 500,
      offset: 100,
      traceId: 'trace/one',
      since: '2026-11-07T10:00:00.000Z',
      until: '2026-11-07T11:00:00.000Z',
    })

    const url = new URL(fetchMock.mock.calls[0][0] as string)
    expect(url.pathname).toBe('/api/v1/observability/audit')
    expect(url.searchParams.get('limit')).toBe('500')
    expect(url.searchParams.get('offset')).toBe('100')
    expect(url.searchParams.get('trace_id')).toBe('trace/one')
    expect(url.searchParams.get('since')).toBe('2026-11-07T10:00:00.000Z')
    expect(url.searchParams.get('until')).toBe('2026-11-07T11:00:00.000Z')
  })

  it('creates a filtered CSV export URL', () => {
    const url = new URL(getAuditExportUrl({
      traceId: 'trace/one',
      since: '2026-11-07T10:00:00.000Z',
    }))

    expect(url.pathname).toBe('/api/v1/observability/audit/export.csv')
    expect(url.searchParams.get('trace_id')).toBe('trace/one')
    expect(url.searchParams.get('since')).toBe('2026-11-07T10:00:00.000Z')
  })

  it('returns a readable error when the API rejects a filter', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 422 })))

    await expect(getAuditEvents()).rejects.toThrow('读取 Audit 事件失败（HTTP 422）。')
  })
})
