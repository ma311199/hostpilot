import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ToolsPanel from './ToolsPanel.vue'

describe('tool catalog page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows registered tools, availability, and approval requirements without secrets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            runtime_configured: true,
            tools: [
              {
                name: 'retrieve_knowledge',
                description: 'Search the configured knowledge service.',
                tool_type: 'retrieval',
                configured: true,
                high_impact: false,
                confirmation_required: false,
                status_message: '已配置，可由 Agent 按需调用。',
              },
              {
                name: 'write_text_file',
                description: 'Write a workspace file.',
                tool_type: 'action',
                configured: true,
                high_impact: true,
                confirmation_required: true,
                status_message: '已配置，可由 Agent 按需调用。',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const wrapper = mount(ToolsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('retrieve_knowledge')
    expect(wrapper.text()).toContain('write_text_file')
    expect(wrapper.text()).toContain('高影响操作 · 每次执行前需审批')
    expect(wrapper.text()).toContain('模型运行时已就绪')
    expect(wrapper.text()).not.toContain('private-key')
  })
})
