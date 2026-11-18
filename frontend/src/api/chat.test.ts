import { afterEach, describe, expect, it, vi } from 'vitest'

import { deleteConversation, upgradeConversationSandbox } from './chat'

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
      conversation_id: 'conversation/one',
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
})
