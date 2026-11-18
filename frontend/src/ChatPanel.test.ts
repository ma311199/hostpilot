import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const chatMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  upgradeConversationSandbox: vi.fn(),
  getConversationMessages: vi.fn(),
  getWorkspace: vi.fn(),
  selectWorkspace: vi.fn(),
  confirmationHandler: null as ((request: {
    request_id: string
    tool_name: string
    summary: string
  }) => void) | null,
  respondToConfirmation: vi.fn(),
  close: vi.fn(),
}))

vi.mock('./api/chat', () => ({
  listConversations: chatMocks.listConversations,
  createConversation: chatMocks.createConversation,
  deleteConversation: chatMocks.deleteConversation,
  upgradeConversationSandbox: chatMocks.upgradeConversationSandbox,
  getConversationMessages: chatMocks.getConversationMessages,
}))

vi.mock('./api/workspace', () => ({
  getWorkspace: chatMocks.getWorkspace,
  selectWorkspace: chatMocks.selectWorkspace,
}))

vi.mock('./api/chatSocket', () => ({
  ChatSocketClient: class {
    get onConfirmation() {
      return chatMocks.confirmationHandler
    }

    set onConfirmation(handler) {
      chatMocks.confirmationHandler = handler
    }

    respondToConfirmation = chatMocks.respondToConfirmation
    close = chatMocks.close
    send = vi.fn()
  },
}))

describe('chat tool approval dialog', () => {
  afterEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    chatMocks.confirmationHandler = null
    chatMocks.listConversations.mockResolvedValue([])
    chatMocks.getWorkspace.mockResolvedValue({
      path: '/home/user/project',
      read_only: false,
      selected: true,
      message: null,
    })
  })

  it('shows a safe summary and sends an explicit rejection', async () => {
    chatMocks.listConversations.mockResolvedValue([])
    const wrapper = mount(ChatPanel)
    await flushPromises()

    chatMocks.confirmationHandler?.({
      request_id: 'confirm-reject',
      tool_name: 'write_text_file',
      summary: '创建工作区文件 `notes.txt`，写入 12 字节。',
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('创建工作区文件 `notes.txt`，写入 12 字节。')
    expect(wrapper.text()).not.toContain('file body')
    await wrapper.get('.approval-reject').trigger('click')

    expect(chatMocks.respondToConfirmation).toHaveBeenCalledWith('confirm-reject', 'rejected')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('sends an explicit cancellation decision', async () => {
    chatMocks.listConversations.mockResolvedValue([])
    const wrapper = mount(ChatPanel)
    await flushPromises()

    chatMocks.confirmationHandler?.({
      request_id: 'confirm-cancel',
      tool_name: 'write_text_file',
      summary: '创建工作区文件 `notes.txt`，写入 12 字节。',
    })
    await wrapper.vm.$nextTick()
    await wrapper.get('.approval-cancel').trigger('click')

    expect(chatMocks.respondToConfirmation).toHaveBeenCalledWith('confirm-cancel', 'cancelled')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('hides internal tool-call and tool-result messages from conversation history', async () => {
    const conversation = {
      conversation_id: 'conversation-1',
      title: 'Approval test',
      created_at: '2026-10-31T00:00:00Z',
      updated_at: '2026-10-31T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    chatMocks.listConversations.mockResolvedValue([conversation])
    chatMocks.getConversationMessages.mockResolvedValue({
      conversation,
      messages: [
        { role: 'user', content: 'Create a file' },
        { role: 'assistant', content: '' },
        { role: 'tool', content: '已写入 approval-smoke.txt（20 字节）。' },
        { role: 'assistant', content: '已完成。文件已写入。' },
      ],
    })

    const wrapper = mount(ChatPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('Create a file')
    expect(wrapper.text()).toContain('已完成。文件已写入。')
    expect(wrapper.text()).not.toContain('已写入 approval-smoke.txt（20 字节）。')
    expect(wrapper.findAll('.message-row')).toHaveLength(2)
    wrapper.unmount()
  })

  it('requests native workspace selection and displays a read-only folder', async () => {
    chatMocks.getWorkspace.mockResolvedValue({
      path: '/home/user/readonly-project',
      read_only: true,
      selected: true,
      message: null,
    })
    chatMocks.selectWorkspace.mockResolvedValue({
      path: '/home/user/new-project',
      read_only: true,
      selected: true,
      message: null,
    })
    const wrapper = mount(ChatPanel)
    await flushPromises()

    expect(wrapper.find('.workspace-path').text()).toContain('/home/user/readonly-project')
    expect(wrapper.find('.workspace-access').text()).toContain('只读访问')
    await wrapper.get('.workspace-select-button').trigger('click')
    await flushPromises()

    expect(chatMocks.selectWorkspace).toHaveBeenCalledOnce()
    expect(wrapper.find('.workspace-path').text()).toContain('/home/user/new-project')
    wrapper.unmount()
  })

  it('searches conversations and groups them by recency', async () => {
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(today.getDate() - 1)
    const earlier = new Date(today)
    earlier.setDate(today.getDate() - 3)
    chatMocks.listConversations.mockResolvedValue([
      { conversation_id: 'today', title: 'Today work', created_at: today.toISOString(), updated_at: today.toISOString(), sandbox_mode: 'read-only' },
      { conversation_id: 'yesterday', title: 'Yesterday plan', created_at: yesterday.toISOString(), updated_at: yesterday.toISOString(), sandbox_mode: 'read-only' },
      { conversation_id: 'earlier', title: 'Older notes', created_at: earlier.toISOString(), updated_at: earlier.toISOString(), sandbox_mode: 'read-only' },
    ])
    const wrapper = mount(ChatPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('今天')
    expect(wrapper.text()).toContain('昨天')
    expect(wrapper.text()).toContain('更早')
    await wrapper.get('input[aria-label="搜索会话"]').setValue('Yesterday')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('Yesterday plan')
    expect(wrapper.find('.conversation-list').text()).not.toContain('Today work')
    wrapper.unmount()
  })

  it('renames and pins a conversation through its overflow menu and restores those preferences', async () => {
    const conversation = {
      conversation_id: 'conversation-menu',
      title: 'Original title',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      sandbox_mode: 'read-only',
    }
    chatMocks.listConversations.mockResolvedValue([conversation])
    chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })

    const wrapper = mount(ChatPanel)
    await flushPromises()
    await wrapper.get('button[aria-label="会话操作 Original title"]').trigger('click')
    expect(wrapper.text()).toContain('重命名')
    expect(wrapper.text()).toContain('置顶')

    await wrapper.get('button[aria-label="重命名会话"]').trigger('click')
    await wrapper.get('input[aria-label="会话名称"]').setValue('Renamed title')
    await wrapper.get('.conversation-rename-form').trigger('submit')
    expect(wrapper.find('.conversation-item').text()).toContain('Renamed title')

    await wrapper.get('button[aria-label="会话操作 Renamed title"]').trigger('click')
    await wrapper.get('button[aria-label="置顶会话"]').trigger('click')
    expect(wrapper.find('.conversation-date-group h2').text()).toBe('置顶')
    wrapper.unmount()

    const restored = mount(ChatPanel)
    await flushPromises()
    expect(restored.find('.conversation-date-group h2').text()).toBe('置顶')
    expect(restored.find('.conversation-item').text()).toContain('Renamed title')
    restored.unmount()
  })

  it('deletes a conversation after confirmation and selects the next conversation', async () => {
    const current = {
      conversation_id: 'conversation-current',
      title: 'Current',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    const previous = {
      conversation_id: 'conversation-previous',
      title: 'Previous',
      created_at: '2026-11-07T00:00:00Z',
      updated_at: '2026-11-07T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    chatMocks.listConversations.mockResolvedValue([current, previous])
    chatMocks.getConversationMessages.mockImplementation(async (id: string) => ({
      conversation: id === current.conversation_id ? current : previous,
      messages: [{ role: 'user', content: `Message for ${id}` }],
    }))
    chatMocks.deleteConversation.mockResolvedValue(undefined)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ChatPanel)
    await flushPromises()

    await wrapper.get('[aria-label="会话操作 Current"]').trigger('click')
    await wrapper.get('[aria-label="删除会话 Current"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledOnce()
    expect(chatMocks.deleteConversation).toHaveBeenCalledWith(current.conversation_id)
    expect(wrapper.text()).not.toContain('Current')
    expect(wrapper.text()).toContain('Previous')
    expect(wrapper.text()).toContain(`Message for ${previous.conversation_id}`)
    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('requires explicit confirmation and persists a session sandbox upgrade', async () => {
    const conversation = {
      conversation_id: 'conversation-sandbox',
      title: 'Sandbox test',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    chatMocks.listConversations.mockResolvedValue([conversation])
    chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
    chatMocks.upgradeConversationSandbox.mockResolvedValue({
      ...conversation,
      sandbox_mode: 'workspace-write',
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ChatPanel)
    await flushPromises()

    await wrapper.get('.sandbox-controls button').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledOnce()
    expect(confirmSpy.mock.calls[0]?.[0]).toContain('不能在本会话中撤销')
    expect(chatMocks.upgradeConversationSandbox).toHaveBeenCalledWith(
      conversation.conversation_id,
      'workspace-write',
    )
    expect(wrapper.text()).toContain('本会话：工作区可写')
    confirmSpy.mockRestore()
    wrapper.unmount()
  })
})
