import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AuditPanel from './AuditPanel.vue'

describe('Audit observability page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists safe records and submits trace/time filters', async () => {
    const event = {
      event_id: 'event-1',
      trace_id: 'trace-1',
      action: 'tool.execute',
      outcome: 'completed',
      resource_type: 'tool',
      resource_id: 'write_text_file',
      reason_code: null,
      occurred_at: '2026-11-07T10:00:00.000Z',
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([event]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(AuditPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Audit 审计')
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('write_text_file')
    expect(wrapper.text()).not.toContain('raw arguments')

    await wrapper.get('input[placeholder="可选，按请求关联"]').setValue('trace-filtered')
    await wrapper.get('input[type="datetime-local"]').setValue('2026-11-07T10:30')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const filteredUrl = new URL(fetchMock.mock.calls.at(-1)?.[0] as string)
    expect(filteredUrl.searchParams.get('trace_id')).toBe('trace-filtered')
    expect(filteredUrl.searchParams.get('limit')).toBe('100')
    expect(filteredUrl.searchParams.get('offset')).toBe('0')
    expect(filteredUrl.searchParams.get('since')).toBe(new Date('2026-11-07T10:30').toISOString())
    expect(wrapper.get('.audit-export-button').attributes('href')).toContain('trace_id=trace-filtered')
    wrapper.unmount()
  })

  it('moves through pages using a stable offset', async () => {
    const page = Array.from({ length: 100 }, (_, index) => ({
      event_id: `event-${index}`,
      trace_id: null,
      action: 'tool.execute',
      outcome: 'completed',
      resource_type: 'tool',
      resource_id: null,
      reason_code: null,
      occurred_at: '2026-11-07T10:00:00.000Z',
    }))
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(page), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(AuditPanel)
    await flushPromises()

    await wrapper.get('.audit-pagination button:last-child').trigger('click')
    await flushPromises()

    const nextUrl = new URL(fetchMock.mock.calls.at(-1)?.[0] as string)
    expect(nextUrl.searchParams.get('offset')).toBe('100')
    expect(wrapper.text()).toContain('第 2 页')
    wrapper.unmount()
  })

  it('shows the empty and error states', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('[]', { status: 200 }))
      .mockResolvedValueOnce(new Response('', { status: 503 })))
    const wrapper = mount(AuditPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('暂无审计记录')

    await wrapper.get('button[type="button"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('HTTP 503')
    wrapper.unmount()
  })
})
