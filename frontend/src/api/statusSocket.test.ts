import { describe, expect, it } from 'vitest'

import { createPingMessage, getStatusSocketUrl, parseStatusMessage } from './statusSocket'

describe('dashboard WebSocket protocol', () => {
  it('uses the local status endpoint by default', () => {
    expect(getStatusSocketUrl('')).toBe('ws://127.0.0.1:8000/api/v1/ws/status')
  })

  it('accepts a valid initial status snapshot', () => {
    expect(
      parseStatusMessage(
        JSON.stringify({
          type: 'status.snapshot',
          data: { api: 'ok', protocol_version: 1 },
        }),
      ),
    ).toEqual({
      type: 'status.snapshot',
      data: { api: 'ok', protocol_version: 1 },
    })
  })

  it('returns null for malformed or unknown messages', () => {
    expect(parseStatusMessage('not-json')).toBeNull()
    expect(parseStatusMessage(JSON.stringify({ type: 'chat.delta', data: 'ignored' }))).toBeNull()
    expect(
      parseStatusMessage(
        JSON.stringify({ type: 'status.snapshot', data: { api: 'down', protocol_version: 1 } }),
      ),
    ).toBeNull()
  })

  it('creates a request-correlated ping envelope', () => {
    expect(JSON.parse(createPingMessage(3))).toEqual({
      type: 'ping',
      request_id: 'dashboard-3',
    })
  })
})
