import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import HarnessSettingsPanel from './HarnessSettingsPanel.vue'

const defaults = {
  max_context_messages: 40,
  max_memory_context_chars: 16000,
  max_request_chars: 32000,
  max_response_chars: 100000,
  request_token_budget: 20000,
  request_tool_call_budget: 5,
  request_retrieval_call_budget: 2,
  request_timeout_seconds: 60,
  plan_max_tasks: 10,
  plan_tool_agent_max_turns: 5,
  plan_tool_candidate_limit: 5,
  plan_result_history_limit: 32,
  plan_result_history_max_chars: 65536,
  routing_minimum_business_confidence: 0.85,
  routing_minimum_business_margin: 0.3,
  routing_minimum_plan_candidate_confidence: 0.5,
  routing_max_plan_candidates: 5,
  routing_max_business_candidates: 32,
}

describe('Harness runtime settings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads current limits and saves the complete runtime configuration', async () => {
    const saved = {
      ...defaults,
      request_token_budget: 12000,
      request_tool_call_budget: 3,
      plan_result_history_limit: 16,
      plan_result_history_max_chars: 24000,
    }
    const fetchMock = vi.fn().mockImplementation((_: string, init?: RequestInit) => (
      Promise.resolve(new Response(
        JSON.stringify(init?.method === 'PUT' ? saved : defaults),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
    ))
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(HarnessSettingsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('上下文与消息限制')
    expect(wrapper.findAll('details.harness-settings-group')).toHaveLength(4)
    expect(wrapper.find('details.harness-settings-group[open]').text()).toContain('上下文与消息限制')
    await wrapper.findAll('summary.harness-settings-group-heading')[1].trigger('click')
    expect(wrapper.text()).toContain('历史成功结果仅供 Planner 参考')
    await wrapper.get('input[aria-label="Plan 历史成功结果条数"]').setValue('16')
    await wrapper.get('input[aria-label="Plan 历史结果总字符数"]').setValue('24000')
    await wrapper.findAll('summary.harness-settings-group-heading')[3].trigger('click')
    await wrapper.get('input[aria-label="Token 预算"]').setValue('12000')
    await wrapper.get('input[aria-label="工具调用次数"]').setValue('3')
    await wrapper.get('input[aria-label="每个子任务最多 ReAct 轮数"]').setValue('5')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const saveCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'PUT')
    expect(saveCall).toBeDefined()
    expect(JSON.parse(saveCall?.[1]?.body as string)).toMatchObject({
      request_token_budget: 12000,
      request_tool_call_budget: 3,
      max_context_messages: 40,
      plan_tool_agent_max_turns: 5,
      plan_result_history_limit: 16,
      plan_result_history_max_chars: 24000,
    })
    expect(wrapper.get('[role="status"]').text()).toContain('立即应用')
    wrapper.unmount()
  })

  it('shows a safe error when configuration cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('private backend detail')))

    const wrapper = mount(HarnessSettingsPanel)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('无法连接后端')
    expect(wrapper.text()).not.toContain('private backend detail')
    wrapper.unmount()
  })
})
