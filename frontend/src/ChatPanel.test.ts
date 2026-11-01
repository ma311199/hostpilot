import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const chatMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  getConversationMessages: vi.fn(),
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
  getConversationMessages: chatMocks.getConversationMessages,
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
    chatMocks.confirmationHandler = null
    chatMocks.listConversations.mockResolvedValue([])
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
})
