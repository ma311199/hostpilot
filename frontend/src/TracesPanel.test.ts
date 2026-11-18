import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TracesPanel from './TracesPanel.vue'

describe('Trace observability page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows safe request summaries and event metadata', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const payload = input.includes('/observability/traces/trace-1')
        ? {
            trace_id: 'trace-1',
            events: [
              {
                event_id: 'event-1',
                span_id: 'span-skill',
                parent_span_id: null,
                name: 'skill.execute',
                occurred_at: '2026-11-07T10:00:00+00:00',
                attributes: { status: 'completed', skill_id: 'business/demo' },
              },
              {
                event_id: 'event-2',
                span_id: 'span-tool',
                parent_span_id: 'span-skill',
                name: 'tool.span',
                occurred_at: '2026-11-07T10:00:01+00:00',
                attributes: { status: 'completed', tool_id: 'write_text_file', duration_ms: 5 },
              },
              {
                event_id: 'event-3',
                span_id: 'span-approval',
                parent_span_id: 'span-tool',
                name: 'tool.confirmation',
                occurred_at: '2026-11-07T10:00:02+00:00',
                attributes: { status: 'allowed', tool_id: 'write_text_file' },
              },
              {
                event_id: 'event-4',
                span_id: 'span-request',
                parent_span_id: null,
                name: 'chat.turn',
                occurred_at: '2026-11-07T10:00:03+00:00',
                attributes: { status: 'completed', route: 'direct', total_tokens: 42 },
              },
            ],
          }
        : [{
            trace_id: 'trace-1',
            first_occurred_at: '2026-11-07T10:00:00+00:00',
            last_occurred_at: '2026-11-07T10:00:01+00:00',
            event_count: 4,
            last_event: 'chat.turn',
            status: 'completed',
            skill_id: null,
          }]
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(TracesPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('chat.turn')
    await wrapper.get('.trace-card').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('请求事件')
    expect(wrapper.text()).toContain('工具 ID')
    expect(wrapper.text()).toContain('write_text_file')
    expect(wrapper.text()).toContain('span-skill')
    expect(wrapper.text()).toContain('span-tool')
    expect(wrapper.text()).toContain('tool.confirmation')
    expect(wrapper.text()).toContain('Token 数')
    expect(wrapper.text()).toContain('42')
    expect(wrapper.text()).not.toContain('private conversation')
    wrapper.unmount()
  })

  it('shows an empty state before trace events exist', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('[]', { status: 200 })))

    const wrapper = mount(TracesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无 Trace')
    wrapper.unmount()
  })

  it('filters and paginates recent traces', async () => {
    const traces = Array.from({ length: 13 }, (_, index) => ({
      trace_id: `trace-${index + 1}`,
      first_occurred_at: '2026-11-07T10:00:00+00:00',
      last_occurred_at: '2026-11-07T10:00:01+00:00',
      event_count: 1,
      last_event: index === 12 ? 'skill.special' : 'chat.turn',
      status: index === 12 ? 'failed' : 'completed',
      skill_id: index === 12 ? 'business/special' : null,
    }))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(traces), { status: 200 })))
    const wrapper = mount(TracesPanel)
    await flushPromises()
    expect(wrapper.findAll('.trace-card')).toHaveLength(12)
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(wrapper.findAll('.trace-card')).toHaveLength(1)
    await wrapper.get('input[aria-label="搜索记录"]').setValue('special')
    expect(wrapper.findAll('.trace-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('trace-13')
    await wrapper.get('select').setValue('completed')
    expect(wrapper.find('.traces-empty').text()).toContain('没有符合条件')
    wrapper.unmount()
  })
})
