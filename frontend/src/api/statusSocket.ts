export interface StatusSnapshot {
  type: 'status.snapshot'
  data: {
    api: 'ok'
    protocol_version: number
  }
}

export interface PongMessage {
  type: 'pong'
  request_id: string
}

export interface ErrorMessage {
  type: 'error'
  data: {
    code: string
  }
}

export type StatusServerMessage = StatusSnapshot | PongMessage | ErrorMessage

const defaultSocketUrl = 'ws://127.0.0.1:8000/api/v1/ws/status'

export function getStatusSocketUrl(
  configuredUrl = import.meta.env.VITE_HOSTPILOT_WS_URL as string | undefined,
): string {
  return configuredUrl?.trim() || defaultSocketUrl
}

export function parseStatusMessage(raw: string): StatusServerMessage | null {
  let message: unknown

  try {
    message = JSON.parse(raw)
  } catch {
    return null
  }

  if (!isRecord(message)) return null

  if (
    message.type === 'status.snapshot' &&
    isRecord(message.data) &&
    message.data.api === 'ok' &&
    typeof message.data.protocol_version === 'number'
  ) {
    return {
      type: 'status.snapshot',
      data: {
        api: 'ok',
        protocol_version: message.data.protocol_version,
      },
    }
  }

  if (message.type === 'pong' && typeof message.request_id === 'string') {
    return { type: 'pong', request_id: message.request_id }
  }

  if (message.type === 'error' && isRecord(message.data) && typeof message.data.code === 'string') {
    return { type: 'error', data: { code: message.data.code } }
  }

  return null
}

export function createPingMessage(sequence: number): string {
  return JSON.stringify({ type: 'ping', request_id: 'dashboard-' + sequence })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
