import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  deleteConversation,
  listPendingToolOutcomes,
  resolveToolOutcome,
  upgradeConversationSandbox,
} from './chat'

describe('Conversation API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('deletes the selected conversation by encoded ID', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteConversation('conversation/one')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/conversation%2Fone',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('submits a one-way sandbox upgrade for the selected conversation', async () => {
    const summary = {
      session_id: 'conversation/one',
      title: 'Planning',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      sandbox_mode: 'danger-full-access',
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(summary), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      upgradeConversationSandbox('conversation/one', 'danger-full-access'),
    ).resolves.toEqual(summary)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/conversation%2Fone/sandbox/upgrade',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ target_mode: 'danger-full-access' }),
      }),
    )
  })

  it('lists sanitized pending tool outcomes for an encoded Session ID', async () => {
    const pending = [{
      tool_call_id: 'call-1',
      tool_name: 'write_text_file',
      created_at: '2026-11-21T10:00:00+00:00',
      state: 'pending',
    }]
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(pending), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listPendingToolOutcomes('session/one')).resolves.toEqual(pending)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/tool-outcomes',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    )
  })

  it('submits a verified result bound to the original tool call ID', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ resolved: true }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      resolveToolOutcome('session-1', 'call/1', {
        resolution: 'completed',
        result_text: 'Verified output',
      }),
    ).resolves.toEqual({ resolved: true })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/session-1/tool-outcomes/call%2F1/resolve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ resolution: 'completed', result_text: 'Verified output' }),
      }),
    )
  })
})
