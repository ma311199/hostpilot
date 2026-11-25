import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const chatMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  upgradeConversationSandbox: vi.fn(),
  getConversationMessages: vi.fn(),
  listPendingToolOutcomes: vi.fn(),
  listRecoverablePlanRuns: vi.fn(),
  listRecoverableChatTurns: vi.fn(),
  resumePlanRun: vi.fn(),
  resumeTurn: vi.fn(),
  resolvePlanInvocation: vi.fn(),
  resolveToolOutcome: vi.fn(),
  getWorkspace: vi.fn(),
  selectWorkspace: vi.fn(),
  confirmationHandler: null as ((request: {
    request_id: string
    tool_name: string
    summary: string
  }) => void) | null,
  respondToConfirmation: vi.fn(),
  taskCacheHandler: null as ((request: {
    request_id: string
    task_id: string
    description: string
    source_turn_id: string
    finished_at: string
    output: unknown
    execution_summary?: {
      tools: { tool_id: string; call_count: number; side_effect_level: string }[]
      confirmation_decisions: string[]
    } | null
  }) => void) | null,
  respondToTaskCacheApproval: vi.fn(),
  close: vi.fn(),
}))

vi.mock('./api/chat', () => ({
  listConversations: chatMocks.listConversations,
  createConversation: chatMocks.createConversation,
  deleteConversation: chatMocks.deleteConversation,
  upgradeConversationSandbox: chatMocks.upgradeConversationSandbox,
  getConversationMessages: chatMocks.getConversationMessages,
  listPendingToolOutcomes: chatMocks.listPendingToolOutcomes,
  listRecoverablePlanRuns: chatMocks.listRecoverablePlanRuns,
  listRecoverableChatTurns: chatMocks.listRecoverableChatTurns,
  resumePlanRun: chatMocks.resumePlanRun,
  resolvePlanInvocation: chatMocks.resolvePlanInvocation,
  resolveToolOutcome: chatMocks.resolveToolOutcome,
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

    get onTaskCacheApproval() {
      return chatMocks.taskCacheHandler
    }

    set onTaskCacheApproval(handler) {
      chatMocks.taskCacheHandler = handler
    }

    respondToConfirmation = chatMocks.respondToConfirmation
    respondToTaskCacheApproval = chatMocks.respondToTaskCacheApproval
    resumePlanRun = chatMocks.resumePlanRun
    resumeTurn = chatMocks.resumeTurn
    close = chatMocks.close
    send = vi.fn()
  },
}))

describe('chat tool approval dialog', () => {
  afterEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    chatMocks.confirmationHandler = null
    chatMocks.taskCacheHandler = null
    chatMocks.listConversations.mockResolvedValue([])
    chatMocks.getWorkspace.mockResolvedValue({
      path: '/home/user/project',
      read_only: false,
      selected: true,
      message: null,
    })
    chatMocks.listPendingToolOutcomes.mockResolvedValue([])
    chatMocks.listRecoverablePlanRuns.mockResolvedValue([])
    chatMocks.listRecoverableChatTurns.mockResolvedValue([])
    chatMocks.resumePlanRun.mockResolvedValue({
      plan_run_id: 'plan-1', state: 'completed', status: 'completed', reason_code: null,
    })
    chatMocks.resumeTurn.mockResolvedValue({
      turn_id: 'turn-1', status: 'completed', assistant_message: 'Recovered', reason_code: null,
    })
    chatMocks.resolvePlanInvocation.mockResolvedValue({ resolved: true })
    chatMocks.resolveToolOutcome.mockResolvedValue({ resolved: true })
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
      session_id: 'conversation-1',
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

  it('blocks chat and submits a verified result for an unresolved side-effecting call', async () => {
    const conversation = {
      session_id: 'session-recovery',
      title: 'Recovery',
      created_at: '2026-11-21T10:00:00Z',
      updated_at: '2026-11-21T10:00:00Z',
      sandbox_mode: 'workspace-write',
    }
    chatMocks.listConversations.mockResolvedValue([conversation])
    chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
    chatMocks.listPendingToolOutcomes
      .mockResolvedValueOnce([{
        tool_call_id: 'call-uncertain',
        tool_name: 'write_text_file',
        created_at: '2026-11-21T10:00:00Z',
        state: 'pending',
      }])
      .mockResolvedValueOnce([])

    const wrapper = mount(ChatPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('需要核实工具执行结果')
    expect(wrapper.text()).toContain('call-uncertain')
    expect((wrapper.get('textarea[aria-label="输入消息"]').element as HTMLTextAreaElement).disabled).toBe(true)
    await wrapper.get('.tool-outcome-item textarea').setValue('目标文件已经写入')
    await wrapper.get('.tool-outcome-complete').trigger('click')
    await flushPromises()

    expect(chatMocks.resolveToolOutcome).toHaveBeenCalledWith(
      conversation.session_id,
      'call-uncertain',
      { resolution: 'completed', result_text: '目标文件已经写入' },
    )
    expect(wrapper.find('.tool-outcome-recovery').exists()).toBe(false)
    wrapper.unmount()
  })

  it('requires explicit confirmation before marking a tool outcome not applied', async () => {
    const conversation = {
      session_id: 'session-no-effect',
      title: 'Recovery',
      created_at: '2026-11-21T10:00:00Z',
      updated_at: '2026-11-21T10:00:00Z',
      sandbox_mode: 'workspace-write',
    }
    chatMocks.listConversations.mockResolvedValue([conversation])
    chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
    chatMocks.listPendingToolOutcomes
      .mockResolvedValueOnce([{
        tool_call_id: 'call-not-applied',
        tool_name: 'remote_action',
        created_at: '2026-11-21T10:00:00Z',
        state: 'pending',
      }])
      .mockResolvedValueOnce([])
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))

    const wrapper = mount(ChatPanel)
    await flushPromises()
    await wrapper.get('.tool-outcome-actions button:first-child').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(chatMocks.resolveToolOutcome).toHaveBeenCalledWith(
      conversation.session_id,
      'call-not-applied',
      { resolution: 'not_applied' },
    )
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
      { session_id: 'today', title: 'Today work', created_at: today.toISOString(), updated_at: today.toISOString(), sandbox_mode: 'read-only' },
      { session_id: 'yesterday', title: 'Yesterday plan', created_at: yesterday.toISOString(), updated_at: yesterday.toISOString(), sandbox_mode: 'read-only' },
      { session_id: 'earlier', title: 'Older notes', created_at: earlier.toISOString(), updated_at: earlier.toISOString(), sandbox_mode: 'read-only' },
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
      session_id: 'conversation-menu',
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
      session_id: 'conversation-current',
      title: 'Current',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    const previous = {
      session_id: 'conversation-previous',
      title: 'Previous',
      created_at: '2026-11-07T00:00:00Z',
      updated_at: '2026-11-07T00:00:00Z',
      sandbox_mode: 'read-only',
    }
    chatMocks.listConversations.mockResolvedValue([current, previous])
    chatMocks.getConversationMessages.mockImplementation(async (id: string) => ({
      conversation: id === current.session_id ? current : previous,
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
    expect(chatMocks.deleteConversation).toHaveBeenCalledWith(current.session_id)
    expect(wrapper.text()).not.toContain('Current')
    expect(wrapper.text()).toContain('Previous')
    expect(wrapper.text()).toContain(`Message for ${previous.session_id}`)
    confirmSpy.mockRestore()
    wrapper.unmount()
  })

  it('requires explicit confirmation and persists a session sandbox upgrade', async () => {
    const conversation = {
      session_id: 'conversation-sandbox',
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
      conversation.session_id,
      'workspace-write',
    )
    expect(wrapper.text()).toContain('本会话：工作区可写')
    confirmSpy.mockRestore()
    wrapper.unmount()
  })
})

it('shows interrupted Plan progress and resumes only after explicit confirmation', async () => {
  const conversation = {
    session_id: 'conversation-plan-recovery',
    title: 'Recovery test',
    created_at: '2026-11-22T00:00:00Z',
    updated_at: '2026-11-22T00:00:00Z',
    sandbox_mode: 'read-only',
  }
  chatMocks.listConversations.mockResolvedValue([conversation])
  chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
  chatMocks.listRecoverablePlanRuns.mockResolvedValue([{
    plan_run_id: 'plan-1',
    goal: 'Prepare a report',
    state: 'interrupted',
    tasks: [{
      task_id: 'task-1', description: 'Read source', status: 'completed',
      output: { summary: 'Source reviewed' }, error_code: null,
    }],
    pending_invocations: [],
    updated_at: '2026-11-22T00:00:00Z',
  }])
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  const wrapper = mount(ChatPanel)
  await flushPromises()

  expect(wrapper.text()).toContain('Prepare a report')
  expect(wrapper.text()).toContain('Read source')
  expect(wrapper.text()).toContain('Source reviewed')
  expect(chatMocks.resumePlanRun).not.toHaveBeenCalled()

  await wrapper.get('.plan-recovery-footer button').trigger('click')
  await flushPromises()

  expect(confirmSpy).toHaveBeenCalledOnce()
  expect(chatMocks.resumePlanRun).toHaveBeenCalledWith('conversation-plan-recovery', 'plan-1')
  expect(wrapper.get('[role="status"]').text()).toContain('Plan 已恢复并完成')
  confirmSpy.mockRestore()
  wrapper.unmount()
})

it('requires a user observation before resuming an unresolved Plan invocation', async () => {
  const conversation = {
    session_id: 'conversation-unresolved-plan',
    title: 'Unresolved recovery',
    created_at: '2026-11-22T00:00:00Z',
    updated_at: '2026-11-22T00:00:00Z',
    sandbox_mode: 'read-only',
  }
  chatMocks.listConversations.mockResolvedValue([conversation])
  chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
  chatMocks.listRecoverablePlanRuns.mockResolvedValue([{
    plan_run_id: 'plan-uncertain',
    goal: 'Send a report',
    state: 'awaiting_reconciliation',
    tasks: [],
    pending_invocations: [{
      invocation_id: 'invocation-uncertain',
      tool_id: 'send_message',
      retry_class: 'non_idempotent_write',
      state: 'awaiting_reconciliation',
    }],
    updated_at: '2026-11-22T00:00:00Z',
  }])
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
  const wrapper = mount(ChatPanel)
  await flushPromises()

  expect(wrapper.text()).toContain('需要人工核实')
  expect(wrapper.get('.plan-recovery-footer button').attributes('disabled')).toBeDefined()
  await wrapper.get('textarea[placeholder="填写实际观察到的结果；HostPilot 无法独立验证它"]').setValue('Observed message id 7')
  await wrapper.get('.plan-recovery-actions .plan-recovery-primary').trigger('click')
  await flushPromises()

  expect(chatMocks.resolvePlanInvocation).toHaveBeenCalledWith(
    'conversation-unresolved-plan',
    'plan-uncertain',
    'invocation-uncertain',
    { resolution: 'executed', result_text: 'Observed message id 7' },
  )
  expect(chatMocks.resumePlanRun).not.toHaveBeenCalled()
  expect(confirmSpy).not.toHaveBeenCalled()
  confirmSpy.mockRestore()
  wrapper.unmount()
})

it('shows a recoverable Chat Turn and resumes only after an explicit click', async () => {
  const conversation = {
    session_id: 'conversation-turn-recovery',
    title: 'Interrupted chat',
    created_at: '2026-11-23T00:00:00Z',
    updated_at: '2026-11-23T00:00:00Z',
    sandbox_mode: 'read-only',
  }
  chatMocks.listConversations.mockResolvedValue([conversation])
  chatMocks.getConversationMessages.mockResolvedValue({ conversation, messages: [] })
  chatMocks.listRecoverableChatTurns.mockResolvedValue([{
    turn_id: 'turn-1',
    started_at: '2026-11-22T23:00:00Z',
    recoverable: true,
    reason_code: null,
  }])
  chatMocks.listRecoverablePlanRuns.mockResolvedValue([])
  const wrapper = mount(ChatPanel)
  await flushPromises()

  expect(wrapper.text()).toContain('有对话在中断后等待恢复')
  expect(wrapper.get('.turn-recovery time').attributes('datetime')).toBe('2026-11-22T23:00:00Z')
  expect(chatMocks.resumeTurn).not.toHaveBeenCalled()

  await wrapper.get('.turn-recovery button').trigger('click')
  await flushPromises()

  expect(chatMocks.resumeTurn).toHaveBeenCalledWith('conversation-turn-recovery', 'turn-1')
  expect(chatMocks.listRecoverableChatTurns).toHaveBeenCalledTimes(2)
  expect(wrapper.get('[role="status"]').text()).toContain('对话已恢复并完成')
  wrapper.unmount()
})

it('shows successful cached task output and requires an explicit reuse or execute choice', async () => {
  const wrapper = mount(ChatPanel)
  chatMocks.taskCacheHandler?.({
    request_id: 'cache-1',
    task_id: 'inspect',
    description: 'Read project status',
    source_turn_id: 'turn-old',
    finished_at: '2026-11-22T10:00:00Z',
    output: { status: 'ready' },
    execution_summary: {
      tools: [
        { tool_id: 'write_text_file', call_count: 1, side_effect_level: 'irreversible' },
      ],
      confirmation_decisions: ['approved'],
    },
  })
  await flushPromises()

  expect(wrapper.text()).toContain('发现已完成的相同子任务')
  expect(wrapper.text()).toContain('write_text_file')
  expect(wrapper.text()).toContain('不可逆')
  expect(wrapper.text()).toContain('人工确认：批准 1 次')
  expect(wrapper.text()).toContain('"status": "ready"')
  await wrapper.get('button.approval-reject').trigger('click')
  expect(chatMocks.respondToTaskCacheApproval).toHaveBeenCalledWith('cache-1', 'execute')

  chatMocks.taskCacheHandler?.({
    request_id: 'cache-2',
    task_id: 'inspect',
    description: 'Read project status',
    source_turn_id: 'turn-old',
    finished_at: '2026-11-22T10:00:00Z',
    output: { status: 'ready' },
    execution_summary: null,
  })
  await wrapper.vm.$nextTick()
  expect(wrapper.text()).toContain('执行摘要不可用')
  await wrapper.get('button.approval-accept').trigger('click')
  expect(chatMocks.respondToTaskCacheApproval).toHaveBeenLastCalledWith('cache-2', 'reuse')
  wrapper.unmount()
})
