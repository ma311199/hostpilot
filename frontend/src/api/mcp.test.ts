import { afterEach, describe, expect, it, vi } from 'vitest'

import { getMCPServers, getToolConnections, saveMCPServer } from './mcp'

describe('MCP connection API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads safe connection status from the local API', async () => {
    const payload = {
      connections: [
        {
          connection_id: 'builtin/local',
          transport: 'in_process',
          state: 'connected',
          discovered_tool_count: 4,
          error_code: null,
        },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getToolConnections()).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/mcp/connections',
      { headers: { 'Content-Type': 'application/json' } },
    )
  })

  it('reports an unavailable API without exposing request details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network detail')))

    await expect(getToolConnections()).rejects.toThrow('无法连接后端')
  })

  it('loads MCP metadata without any API key value', async () => {
    const payload = {
      servers: [{
        name: 'research',
        url: 'https://mcp.example/mcp',
        timeout_seconds: 15,
        enabled: true,
        api_key_header: 'Authorization',
        api_key_configured: false,
        managed: true,
        connection_state: 'waiting_for_credentials',
        error_code: null,
      }],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    ))

    await expect(getMCPServers()).resolves.toEqual(payload)
  })

  it('submits the API key only in the local configuration request', async () => {
    const payload = { servers: [] }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await saveMCPServer({
      name: 'research',
      transport: 'streamable_http',
      url: 'https://mcp.example/mcp',
      command: null,
      args: [],
      cwd: null,
      timeout_seconds: 15,
      enabled: true,
      api_key_header: 'Authorization',
      api_key_env: null,
      api_key: 'private-test-key',
    })

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      api_key: 'private-test-key',
    })
  })
})
