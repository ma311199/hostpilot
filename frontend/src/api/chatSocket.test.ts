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
    ['tool_confirmation_rejected', '你已拒绝此操作，文件未更改。'],
    ['tool_confirmation_cancelled', '你已取消此操作，文件未更改。'],
    ['tool_confirmation_timed_out', '审批等待超时，文件未更改。'],
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
})
