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
                transport: 'in_process',
                trust_level: 'third_party',
                execution_env: 'service',
                execution_location: 'HostPilot 进程内调用外部服务',
                access_summary: '将查询发送到后端配置的 RAG 服务。',
                sandbox_status: '无 OS 沙箱',
                configured: true,
                high_impact: false,
                confirmation_required: false,
                status_message: '已配置，可由 Agent 按需调用。',
              },
              {
                name: 'write_text_file',
                description: 'Write a workspace file.',
                tool_type: 'action',
                transport: 'in_process',
                trust_level: 'host',
                execution_env: 'host',
                execution_location: 'HostPilot 进程内',
                access_summary: '在工作区创建或覆盖 UTF-8 文本文件。',
                sandbox_status: '无 OS 沙箱',
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
    expect(wrapper.text()).toContain('能力范围')
    expect(wrapper.text()).toContain('在工作区创建或覆盖 UTF-8 文本文件。')
    expect(wrapper.text()).toContain('HostPilot 进程内')
    expect(wrapper.text()).toContain('无 OS 沙箱')
    expect(wrapper.text()).toContain('高影响操作 · 每次执行前需审批')
    expect(wrapper.text()).toContain('模型运行时已就绪')
    expect(wrapper.text()).not.toContain('private-key')
  })

  it('filters and paginates tools, resets the page, and exposes a long description on demand', async () => {
    const tools = Array.from({ length: 14 }, (_, index) => ({
      name: `tool_${index + 1}`,
      description: index === 0 ? 'full description '.repeat(20) : `description ${index + 1}`,
      tool_type: index % 2 ? 'action' : 'retrieval',
      transport: 'in_process', trust_level: 'host', execution_env: 'host',
      execution_location: 'HostPilot 进程内', access_summary: '本地能力',
      sandbox_status: '无 OS 沙箱', configured: true, high_impact: false,
      confirmation_required: false, status_message: '可调用',
    }))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ runtime_configured: true, tools }))))
    const wrapper = mount(ToolsPanel)
    await flushPromises()

    expect(wrapper.findAll('.tool-card')).toHaveLength(12)
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(wrapper.findAll('.tool-card')).toHaveLength(2)
    await wrapper.get('input[type="search"]').setValue('description 3')
    expect(wrapper.findAll('.tool-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('tool_3')
    await wrapper.get('input[type="search"]').setValue('tool_1')
    await wrapper.get('input[type="search"]').setValue('')
    await wrapper.get('.tool-description-toggle').trigger('click')
    expect(wrapper.get('.tool-description').classes()).toContain('tool-description-expanded')
  })
})
