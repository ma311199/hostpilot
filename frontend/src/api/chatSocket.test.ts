import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatSocketClient } from './chatSocket'

class FakeWebSocket {
  static readonly OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  readonly sent: string[] = []

  constructor() {
    FakeWebSocket.instances.push(this)
  }

  send(message: string): void {
    this.sent.push(message)
  }

  close(): void {
    this.readyState = 3
  }
}

describe('chat WebSocket confirmation outcomes', () => {
  afterEach(() => {
    FakeWebSocket.instances = []
    vi.unstubAllGlobals()
  })

  it.each([
    ['tool_confirmation_rejected', '你已拒绝此操作，本次操作未执行。'],
    ['tool_confirmation_cancelled', '你已取消此操作，本次操作未执行。'],
    ['tool_confirmation_timed_out', '审批等待超时，本次操作未执行。'],
    [
      'sandbox_unavailable',
      '沙箱运行环境不可用。请到“工具”页面查看缺少的依赖，并按 Linux 安装指南安装。',
    ],
    [
      'remote_tool_outcome_unknown',
      '远程工具调用超时，远端执行结果未知。请先检查目标系统状态，再决定是否重试。',
    ],
  ])('shows a specific message for %s', async (code, expectedMessage) => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new ChatSocketClient()
    const pending = client.send('conversation-1', 'write a file')
    const socket = FakeWebSocket.instances[0]
    socket.readyState = FakeWebSocket.OPEN
    socket.onopen?.(new Event('open'))
    await vi.waitFor(() => expect(socket.sent).toHaveLength(1))
    socket.onmessage?.({
      data: JSON.stringify({ type: 'chat.error', data: { code } }),
    } as MessageEvent<string>)

    await expect(pending).rejects.toThrow(expectedMessage)
    client.close()
  })

  it('resumes a Plan over the approval-capable socket and returns its result', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new ChatSocketClient()
    const pending = client.resumePlanRun('conversation-1', 'plan-1')
    const socket = FakeWebSocket.instances[0]
    socket.readyState = FakeWebSocket.OPEN
    socket.onopen?.(new Event('open'))
    await vi.waitFor(() => expect(socket.sent).toHaveLength(1))

    expect(JSON.parse(socket.sent[0] ?? '{}')).toEqual({
      type: 'plan.resume',
      session_id: 'conversation-1',
      plan_run_id: 'plan-1',
    })
    socket.onmessage?.({
      data: JSON.stringify({
        type: 'tool.confirmation.request',
        data: { request_id: 'approval-1', tool_name: 'send_message', summary: 'Send report' },
      }),
    } as MessageEvent<string>)
    expect(socket.sent).toHaveLength(1)
    client.respondToConfirmation('approval-1', 'approved')
    expect(JSON.parse(socket.sent[1] ?? '{}')).toEqual({
      type: 'tool.confirmation.decision',
      request_id: 'approval-1',
      decision: 'approved',
    })
    socket.onmessage?.({
      data: JSON.stringify({
        type: 'plan.resume.response',
        data: { plan_run_id: 'plan-1', state: 'completed', status: 'completed', reason_code: null },
      }),
    } as MessageEvent<string>)

    await expect(pending).resolves.toEqual({
      plan_run_id: 'plan-1',
      state: 'completed',
      status: 'completed',
      reason_code: null,
    })
    client.close()
  })
})
