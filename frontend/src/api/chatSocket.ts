import type { ChatResponse } from './chat'

interface ChatResponseMessage {
  type: 'chat.response'
  data: ChatResponse
}

interface ChatErrorMessage {
  type: 'chat.error'
  data: { code: string }
}

export interface ToolConfirmationMessage {
  request_id: string
  tool_name: string
  summary: string
}

interface ToolConfirmationRequestMessage {
  type: 'tool.confirmation.request'
  data: ToolConfirmationMessage
}

type ChatServerMessage = ChatResponseMessage | ChatErrorMessage | ToolConfirmationRequestMessage

const errorMessages: Record<string, string> = {
  budget_exceeded: '本次请求已达到预算上限。',
  conversation_not_found: '会话不存在，请刷新会话列表。',
  invalid_message: '聊天消息格式无效。',
  message_too_large: '消息超过后端允许的长度。',
  request_failed: '聊天请求未能完成，请稍后重试。',
  request_timeout: '请求超时，请稍后重试。',
  runtime_unavailable: '模型运行时未配置，请先点击“模型配置”。',
  tool_denied: '工具调用未通过策略或预算检查。',
  tool_confirmation_rejected: '你已拒绝此操作，文件未更改。',
  tool_confirmation_cancelled: '你已取消此操作，文件未更改。',
  tool_confirmation_timed_out: '审批等待超时，文件未更改。',
  tool_failed: '工具执行失败或返回结果未通过验证。',
}

function getChatSocketUrl(): string {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_CHAT_WS_URL as string | undefined
  if (configuredUrl?.trim()) return configuredUrl.trim()

  const configuredApiUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiUrl = configuredApiUrl?.trim() || 'http://127.0.0.1:8000/api/v1'
  const socketUrl = apiUrl.replace(/^http/, 'ws').replace(/\/$/, '')
  return `${socketUrl}/ws/chat`
}

function parseChatMessage(raw: string): ChatServerMessage | null {
  try {
    const message: unknown = JSON.parse(raw)
    if (typeof message !== 'object' || message === null) return null
    const candidate = message as Record<string, unknown>
    if (candidate.type === 'chat.response' && typeof candidate.data === 'object' && candidate.data) {
      return { type: 'chat.response', data: candidate.data as ChatResponse }
    }
    if (
      candidate.type === 'chat.error' &&
      typeof candidate.data === 'object' &&
      candidate.data &&
      typeof (candidate.data as Record<string, unknown>).code === 'string'
    ) {
      return {
        type: 'chat.error',
        data: { code: (candidate.data as Record<string, string>).code },
      }
    }
    if (
      candidate.type === 'tool.confirmation.request' &&
      typeof candidate.data === 'object' &&
      candidate.data
    ) {
      const data = candidate.data as Record<string, unknown>
      if (
        typeof data.request_id === 'string' &&
        typeof data.tool_name === 'string' &&
        typeof data.summary === 'string'
      ) {
        return {
          type: 'tool.confirmation.request',
          data: {
            request_id: data.request_id,
            tool_name: data.tool_name,
            summary: data.summary,
          },
        }
      }
    }
  } catch {
    return null
  }
  return null
}

export class ChatSocketClient {
  private socket: WebSocket | null = null
  private connecting: Promise<void> | null = null
  private pending: {
    resolve: (response: ChatResponse) => void
    reject: (error: Error) => void
  } | null = null
  onConfirmation: ((request: ToolConfirmationMessage) => void) | null = null

  async send(conversationId: string, message: string): Promise<ChatResponse> {
    await this.connect()
    if (this.pending) throw new Error('上一条消息仍在处理中。')
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('聊天连接已断开，请重试。')
    }

    return new Promise<ChatResponse>((resolve, reject) => {
      this.pending = { resolve, reject }
      try {
        this.socket?.send(JSON.stringify({
          type: 'chat.send',
          conversation_id: conversationId,
          message,
        }))
      } catch {
        this.pending = null
        reject(new Error('无法发送消息，请重试。'))
      }
    })
  }

  close(): void {
    this.socket?.close()
    this.socket = null
    this.rejectPending(new Error('聊天连接已关闭。'))
  }

  respondToConfirmation(
    requestId: string,
    decision: 'approved' | 'rejected' | 'cancelled',
  ): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify({
      type: 'tool.confirmation.decision',
      request_id: requestId,
      decision,
    }))
  }

  private connect(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) return Promise.resolve()
    if (this.connecting) return this.connecting

    this.connecting = new Promise<void>((resolve, reject) => {
      let opened = false
      try {
        const socket = new WebSocket(getChatSocketUrl())
        this.socket = socket
        socket.onopen = () => {
          opened = true
          resolve()
        }
        socket.onmessage = (event: MessageEvent<string>) => this.handleMessage(String(event.data))
        socket.onerror = () => {
          if (!opened) reject(new Error('无法连接聊天服务，请确认后端已启动。'))
        }
        socket.onclose = () => {
          if (this.socket === socket) this.socket = null
          if (!opened) reject(new Error('无法建立聊天连接，请确认后端已启动。'))
          this.rejectPending(new Error('聊天连接中断，请检查会话历史后重试。'))
        }
      } catch {
        reject(new Error('无法创建聊天连接。'))
      }
    }).finally(() => {
      this.connecting = null
    })
    return this.connecting
  }

  private handleMessage(raw: string): void {
    const message = parseChatMessage(raw)
    if (!message) return
    if (message.type === 'tool.confirmation.request') {
      this.onConfirmation?.(message.data)
      return
    }
    if (!this.pending) return
    const pending = this.pending
    this.pending = null
    if (message.type === 'chat.response') {
      pending.resolve(message.data)
    } else {
      pending.reject(new Error(errorMessages[message.data.code] || '聊天请求失败。'))
    }
  }

  private rejectPending(error: Error): void {
    if (!this.pending) return
    const pending = this.pending
    this.pending = null
    pending.reject(error)
  }
}
