import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import McpConnectionsPanel from './McpConnectionsPanel.vue'

describe('MCP connections page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows connection state and discovered tool counts without endpoint secrets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string) => Promise.resolve(
        new Response(
          JSON.stringify(input.endsWith('/mcp/servers') ? { servers: [] } : {
            connections: [
              {
                connection_id: 'builtin/local',
                transport: 'in_process',
                state: 'connected',
                discovered_tool_count: 4,
                error_code: null,
              },
              {
                connection_id: 'research',
                transport: 'streamable_http',
                state: 'failed',
                discovered_tool_count: 0,
                error_code: 'TimeoutError',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )),
    )

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('builtin/local')
    expect(wrapper.text()).toContain('进程内 MCP')
    expect(wrapper.text()).toContain('发现工具')
    expect(wrapper.text()).toContain('4')
    expect(wrapper.text()).toContain('连接失败')
    expect(wrapper.text()).toContain('TimeoutError')
    expect(wrapper.text()).not.toContain('private-token')
    expect(wrapper.text()).not.toContain('https://')
  })

  it('restores remote metadata and keeps credential entry blank', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string) => Promise.resolve(
        new Response(
          JSON.stringify(input.endsWith('/mcp/servers') ? {
            servers: [{
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
              api_key_configured: false,
              managed: true,
              connection_state: 'waiting_for_credentials',
              error_code: null,
            }],
          } : { connections: [] }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )),
    )

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('research')
    expect(wrapper.text()).toContain('等待 API Key')
    expect(wrapper.text()).toContain('需要重新输入 API Key')
    await wrapper.get('.server-row-actions button').trigger('click')
    expect((wrapper.get('input[placeholder="重启后请重新输入"]').element as HTMLInputElement).value)
      .toBe('')
  })

  it('keeps the editor closed until requested and switches between form and JSON modes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string) => Promise.resolve(
        new Response(
          JSON.stringify(input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )),
    )

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()

    expect(wrapper.find('.server-form').exists()).toBe(false)
    expect(wrapper.find('.server-import-form').exists()).toBe(false)

    await wrapper.get('.server-management-heading-actions button').trigger('click')
    expect(wrapper.find('.server-form').exists()).toBe(true)
    await wrapper.get('.server-config-mode button:nth-child(2)').trigger('click')
    expect(wrapper.find('.server-form').exists()).toBe(false)
    expect(wrapper.find('.server-import-form').exists()).toBe(true)

    await wrapper.get('.server-config-mode button:nth-child(1)').trigger('click')
    expect(wrapper.find('.server-form').exists()).toBe(true)
    expect(wrapper.find('.server-import-form').exists()).toBe(false)
    wrapper.unmount()
  })

  it('submits STDIO command and arguments as separate configuration fields', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const result = input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }
      return Promise.resolve(new Response(JSON.stringify(result), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('input[placeholder="例如 research"]').setValue('filesystem')
    await wrapper.get('select').setValue('stdio')
    await wrapper.get('.server-form textarea').setValue(
      '-y\n@modelcontextprotocol/server-filesystem\n/tmp/project',
    )
    await wrapper.get('.server-form').trigger('submit')
    await flushPromises()

    const saveCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'PUT')
    expect(saveCall).toBeDefined()
    expect(JSON.parse(saveCall?.[1]?.body as string)).toMatchObject({
      name: 'filesystem',
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp/project'],
      url: null,
      api_key_env: null,
    })
    wrapper.unmount()
  })

  it('imports mcpServers JSON as STDIO command and argument fields', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const result = input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }
      return Promise.resolve(new Response(JSON.stringify(result), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('.server-config-mode button:nth-child(2)').trigger('click')
    await wrapper.get('[aria-label="导入 MCP Server JSON"] textarea').setValue(JSON.stringify({
      mcpServers: {
        filesystem: {
          command: 'npx',
          args: ['-y', '@modelcontextprotocol/server-filesystem', '/path/to/project'],
        },
      },
    }))
    await wrapper.get('[aria-label="导入 MCP Server JSON"]').trigger('submit')
    await flushPromises()

    const saveCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'PUT')
    expect(saveCall).toBeDefined()
    expect(JSON.parse(saveCall?.[1]?.body as string)).toMatchObject({
      name: 'filesystem',
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '/path/to/project'],
      url: null,
      api_key_env: null,
    })
    expect(wrapper.text()).toContain('已导入 1 个 MCP Server 配置。')
    wrapper.unmount()
  })

  it('rejects JSON imports containing credentials without sending them', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const result = input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }
      return Promise.resolve(new Response(JSON.stringify(result), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('.server-config-mode button:nth-child(2)').trigger('click')
    await wrapper.get('[aria-label="导入 MCP Server JSON"] textarea').setValue(JSON.stringify({
      mcpServers: {
        private: { command: 'npx', args: ['-y', 'private-server'], env: { API_KEY: 'secret' } },
      },
    }))
    await wrapper.get('[aria-label="导入 MCP Server JSON"]').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('包含凭证字段')
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'PUT')).toBe(false)
    expect(fetchMock.mock.calls.flatMap((call) => call[1]?.body || []).join('')).not.toContain('secret')
    wrapper.unmount()
  })

  it('rejects credential-shaped STDIO args before saving or importing', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const result = input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }
      return Promise.resolve(new Response(JSON.stringify(result), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('input[placeholder="例如 research"]').setValue('private')
    await wrapper.get('select').setValue('stdio')
    await wrapper.get('.server-form textarea').setValue('--api-key\nprivate-token-value')
    await wrapper.get('.server-form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('凭证不能放在 STDIO args 中')
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'PUT')).toBe(false)
    expect(fetchMock.mock.calls.flatMap((call) => call[1]?.body || []).join(''))
      .not.toContain('private-token-value')

    await wrapper.get('.server-config-mode button:nth-child(2)').trigger('click')
    await wrapper.get('[aria-label="导入 MCP Server JSON"] textarea').setValue(JSON.stringify({
      mcpServers: {
        private: {
          command: 'npx',
          args: ['--header=Authorization: Bearer private-json-token', 'MCP_ACCESS_TOKEN=private-env-token'],
        },
      },
    }))
    await wrapper.get('[aria-label="导入 MCP Server JSON"]').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('凭证不能放在 args 中')
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'PUT')).toBe(false)
    wrapper.unmount()
  })

  it('rejects HTTP URLs containing credentials before submitting the import', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      const result = input.endsWith('/mcp/servers') ? { servers: [] } : { connections: [] }
      return Promise.resolve(new Response(JSON.stringify(result), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()
    await wrapper.get('.server-management-heading-actions button').trigger('click')
    await wrapper.get('.server-config-mode button:nth-child(2)').trigger('click')
    await wrapper.get('[aria-label="导入 MCP Server JSON"] textarea').setValue(JSON.stringify({
      mcpServers: {
        private: { url: 'https://mcp.example/mcp?access_token=secret' },
      },
    }))
    await wrapper.get('[aria-label="导入 MCP Server JSON"]').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('URL 不可包含凭证')
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'PUT')).toBe(false)
    wrapper.unmount()
  })

  it('shows a service error when connection status cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network detail')))

    const wrapper = mount(McpConnectionsPanel)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('无法连接后端')
    expect(wrapper.text()).not.toContain('network detail')
  })
})
