import { afterEach, describe, expect, it, vi } from 'vitest'

import { getWorkspace, selectWorkspace } from './workspace'

describe('Workspace API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads the selected directory and read-only status', async () => {
    const payload = {
      path: '/home/user/project',
      read_only: true,
      selected: true,
      message: null,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getWorkspace()).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/v1/workspace', {
      headers: { 'Content-Type': 'application/json' },
    })
  })

  it('opens the native folder picker endpoint', async () => {
    const payload = {
      path: '/home/user/new-project',
      read_only: false,
      selected: true,
      message: null,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(selectWorkspace()).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/workspace/select',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
