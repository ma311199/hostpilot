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
    expect(wrapper.text()).toContain('受控主路径')
    expect(wrapper.text()).toContain('Tool Registry、Policy Engine 与验证模块')
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
})
