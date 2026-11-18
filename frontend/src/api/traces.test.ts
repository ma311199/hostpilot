import { afterEach, describe, expect, it, vi } from 'vitest'

import { getTrace, getTraces } from './traces'

describe('Trace API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists recent request traces with a bounded limit', async () => {
    const payload = [{
      trace_id: 'trace-1',
      first_occurred_at: '2026-11-07T10:00:00+00:00',
      last_occurred_at: '2026-11-07T10:00:01+00:00',
      event_count: 2,
      last_event: 'chat.turn',
      status: 'completed',
      skill_id: null,
    }]
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getTraces(25)).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/observability/traces?limit=25',
    )
  })

  it('loads events for an encoded trace identifier', async () => {
    const payload = { trace_id: 'trace/1', events: [] }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getTrace('trace/1')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/observability/traces/trace%2F1',
    )
  })
})
