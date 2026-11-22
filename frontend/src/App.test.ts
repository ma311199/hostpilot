import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

class FakeWebSocket {
  static readonly OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  readonly sent: string[] = []
  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(message: string): void {
    this.sent.push(message)
  }

  close(): void {
    this.readyState = 3
    this.onclose?.(new CloseEvent('close'))
  }
}

describe('project progress dashboard', () => {
  afterEach(() => {
    FakeWebSocket.instances = []
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('shows project phases and updates backend state from the WebSocket snapshot', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const wrapper = mount(App)
    const connection = FakeWebSocket.instances[0]

    expect(connection.url).toBe('ws://127.0.0.1:8000/api/v1/ws/status')
    expect(wrapper.text()).toContain('YAML Skill 注册表与路由')
    expect(wrapper.text()).toContain('内置进程内 MCP、本地 STDIO 与远程 Streamable HTTP Client')
    expect(wrapper.text()).toContain('导入标准 mcpServers JSON 配置')
    expect(wrapper.text()).toContain('正在连接')

    connection.readyState = FakeWebSocket.OPEN
    connection.onopen?.(new Event('open'))
    connection.onmessage?.({
      data: JSON.stringify({
        type: 'status.snapshot',
        data: { api: 'ok', protocol_version: 1 },
      }),
    } as MessageEvent<string>)
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('后端在线')
    expect(connection.sent).toHaveLength(1)

    wrapper.unmount()
  })

  it('reconnects to the status endpoint after the socket closes', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const wrapper = mount(App)
    const firstConnection = FakeWebSocket.instances[0]

    firstConnection.close()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('后端离线')

    await vi.advanceTimersByTimeAsync(1_000)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(FakeWebSocket.instances[1].url).toBe(firstConnection.url)

    wrapper.unmount()
  })

  it('opens the registered tools page from the main navigation', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            runtime_configured: true,
            tools: [
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
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button').find((button) => button.text() === '工具')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('工具目录')
    expect(wrapper.text()).toContain('write_text_file')
    expect(wrapper.text()).toContain('高影响操作 · 每次执行前需审批')
    wrapper.unmount()
  })

  it('opens the MCP connection status page from the main navigation', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string) => Promise.resolve(
        new Response(
          JSON.stringify(input.endsWith('/mcp/servers') ? { servers: [] } : {
            connections: [
              {
                connection_id: 'builtin/local',
                transport: 'in_process',
                state: 'disconnected',
                discovered_tool_count: 0,
                error_code: null,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )),
    )
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button').find((button) => button.text() === 'MCP 连接')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('MCP 连接')
    expect(wrapper.text()).toContain('builtin/local')
    expect(wrapper.text()).toContain('未连接')
    wrapper.unmount()
  })

  it('opens the Skill library from the main navigation', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            skills: [
              {
                skill_id: 'business/code-review-check',
                name: 'Code review',
                description: 'Review pasted code.',
                execution_format: 'yaml',
                enabled: true,
                source: 'bundled',
              },
            ],
            invalid_package_count: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button').find((button) => button.text() === 'Skill 库')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Skill 库')
    expect(wrapper.text()).toContain('business/code-review-check')
    wrapper.unmount()
  })

  it('opens the Trace page and loads recent request metadata', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string) => Promise.resolve(
        new Response(
          JSON.stringify(input.includes('/observability/traces?') ? [{
            trace_id: 'trace-1',
            first_occurred_at: '2026-11-07T10:00:00+00:00',
            last_occurred_at: '2026-11-07T10:00:01+00:00',
            event_count: 1,
            last_event: 'chat.turn',
            status: 'completed',
            skill_id: null,
          }] : []),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )),
    )
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button').find((button) => button.text() === 'Trace')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Trace 观测')
    expect(wrapper.text()).toContain('trace-1')
    wrapper.unmount()
  })

  it('opens the read-only Audit page from the main navigation', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('[]', { status: 200 })))
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button').find((button) => button.text() === 'Audit')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Audit 审计')
    expect(wrapper.text()).toContain('暂无审计记录')
    wrapper.unmount()
  })

  it('opens Harness configuration from the main navigation', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        max_context_messages: 40,
        max_memory_context_chars: 16000,
        max_request_chars: 32000,
        max_response_chars: 100000,
        request_token_budget: 20000,
        request_tool_call_budget: 5,
        request_retrieval_call_budget: 2,
        request_timeout_seconds: 60,
        plan_result_history_limit: 32,
        plan_result_history_max_chars: 65536,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })),
    )
    const wrapper = mount(App)

    await wrapper.get('nav').findAll('button')
      .find((button) => button.text() === 'Harness 配置')?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('上下文与消息限制')
    expect(wrapper.text()).toContain('单次请求预算')
    wrapper.unmount()
  })

  it('completes the browser management acceptance flow across configuration pages', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const savedConfiguration = {
      max_context_messages: 40,
      max_memory_context_chars: 16000,
      max_request_chars: 32000,
      max_response_chars: 100000,
      request_token_budget: 20000,
      request_tool_call_budget: 5,
      request_retrieval_call_budget: 2,
      request_timeout_seconds: 60,
      plan_result_history_limit: 32,
      plan_result_history_max_chars: 65536,
    }
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      let payload: unknown = {}
      if (input.endsWith('/harness/configuration')) {
        if (init?.method === 'PUT') {
          Object.assign(savedConfiguration, JSON.parse(init.body as string))
        }
        payload = savedConfiguration
      } else if (input.endsWith('/mcp/servers')) {
        payload = { servers: [] }
      } else if (input.endsWith('/mcp/connections')) {
        payload = { connections: [] }
      } else if (input.endsWith('/skills')) {
        payload = { skills: [], invalid_package_count: 0 }
      } else if (input.includes('/observability/traces?')) {
        payload = []
      } else if (input.includes('/observability/audit?')) {
        payload = []
      }
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    const navigate = async (label: string): Promise<void> => {
      const button = wrapper.get('nav').findAll('button')
        .find((candidate) => candidate.text() === label)
      await button?.trigger('click')
      await flushPromises()
    }

    await navigate('Harness 配置')
    await wrapper.get('input[type="number"]').setValue('25')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.harness-settings-success').text()).toContain('立即应用')

    await navigate('MCP 连接')
    expect(wrapper.text()).toContain('MCP Server 配置')
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('[aria-label="MCP 配置模式"] button:nth-child(2)').trigger('click')
    expect(wrapper.find('.server-import-form').exists()).toBe(true)
    expect(wrapper.find('.server-form').exists()).toBe(false)

    await navigate('Skill 库')
    expect(wrapper.text()).toContain('Skill 库')
    await navigate('Trace')
    expect(wrapper.text()).toContain('暂无 Trace')
    await navigate('Audit')
    expect(wrapper.text()).toContain('暂无审计记录')

    await navigate('Harness 配置')
    expect((wrapper.get('input[type="number"]').element as HTMLInputElement).value).toBe('25')
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'PUT')).toBe(true)
    wrapper.unmount()
  })
})
