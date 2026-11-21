<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  createConversation,
  deleteConversation,
  getConversationMessages,
  listConversations,
  upgradeConversationSandbox,
  type ConversationMessage,
  type ConversationSummary,
  type SandboxMode,
} from './api/chat'
import { getWorkspace, selectWorkspace, type WorkspaceStatus } from './api/workspace'
import { ChatSocketClient, type ToolConfirmationMessage } from './api/chatSocket'

const conversations = ref<ConversationSummary[]>([])
const activeSessionId = ref<string | null>(null)
const messages = ref<ConversationMessage[]>([])
const draft = ref('')
const loading = ref(false)
const sending = ref(false)
const deletingSessionId = ref<string | null>(null)
const upgradingSandbox = ref(false)
const selectingWorkspace = ref(false)
const errorMessage = ref('')
const workspaceError = ref('')
const workspace = ref<WorkspaceStatus | null>(null)
const messageList = ref<HTMLElement | null>(null)
const composerInput = ref<HTMLTextAreaElement | null>(null)
const chatSocket = new ChatSocketClient()
const pendingApproval = ref<ToolConfirmationMessage | null>(null)
interface ConversationPreference {
  title?: string
  pinned?: boolean
}

const conversationPreferenceKey = 'hostpilot.conversation-preferences.v1'
const conversationPreferences = ref<Record<string, ConversationPreference>>(loadConversationPreferences())
const openSessionMenuId = ref<string | null>(null)
const renamingSessionId = ref<string | null>(null)
const conversationNameDraft = ref('')
const conversationNameInput = ref<HTMLInputElement[]>([])
const visibleMessages = computed(() =>
  messages.value.filter(
    (message) =>
      (message.role === 'user' || message.role === 'assistant') && message.content.trim().length > 0,
  ),
)
const displayConversations = computed(() => conversations.value.map((conversation) => {
  const preference = conversationPreferences.value[conversation.session_id]
  return {
    ...conversation,
    title: preference?.title || conversation.title,
    pinned: preference?.pinned || false,
  }
}))
const activeConversation = computed(() =>
  displayConversations.value.find((conversation) => conversation.session_id === activeSessionId.value),
)
const conversationQuery = ref('')
const filteredConversations = computed(() => {
  const query = conversationQuery.value.trim().toLocaleLowerCase()
  return displayConversations.value.filter((conversation) => !query || conversation.title.toLocaleLowerCase().includes(query))
})
const conversationGroups = computed(() => {
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const sameDay = (value: string, target: Date) => {
    const date = new Date(value)
    return !Number.isNaN(date.getTime()) && date.toDateString() === target.toDateString()
  }
  const unpinned = filteredConversations.value.filter((item) => !item.pinned)
  return [
    { label: '置顶', items: filteredConversations.value.filter((item) => item.pinned) },
    { label: '今天', items: unpinned.filter((item) => sameDay(item.updated_at, today)) },
    { label: '昨天', items: unpinned.filter((item) => sameDay(item.updated_at, yesterday)) },
    { label: '更早', items: unpinned.filter((item) => !sameDay(item.updated_at, today) && !sameDay(item.updated_at, yesterday)) },
  ].filter((group) => group.items.length)
})
const suggestions = [
  '帮我梳理一下今天的工作计划',
  '解释一下 Agent 的工具调用流程',
  '根据项目路线总结下一步重点',
  '/read README.md',
  '/write approval-demo.txt\n这条写入会先等待人工审批。',
]

chatSocket.onConfirmation = (request) => {
  pendingApproval.value = request
}

async function refreshConversations(): Promise<void> {
  conversations.value = await listConversations()
}

function loadConversationPreferences(): Record<string, ConversationPreference> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(conversationPreferenceKey) || '{}')
    if (!isRecord(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter(([, preference]) => (
      isRecord(preference)
      && (typeof preference.title === 'string' || typeof preference.pinned === 'boolean')
    ))) as Record<string, ConversationPreference>
  } catch {
    return {}
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function persistConversationPreferences(): void {
  try {
    window.localStorage.setItem(conversationPreferenceKey, JSON.stringify(conversationPreferences.value))
  } catch {
    // Keep the in-memory preferences when browser storage is unavailable.
  }
}

function toggleConversationMenu(sessionId: string): void {
  openSessionMenuId.value = openSessionMenuId.value === sessionId ? null : sessionId
  renamingSessionId.value = null
}

async function beginRenameConversation(conversation: ConversationSummary): Promise<void> {
  renamingSessionId.value = conversation.session_id
  conversationNameDraft.value = conversation.title
  await nextTick()
  conversationNameInput.value[0]?.focus()
}

function saveConversationName(sessionId: string): void {
  const title = conversationNameDraft.value.trim().slice(0, 200)
  if (!title) return
  conversationPreferences.value = {
    ...conversationPreferences.value,
    [sessionId]: { ...conversationPreferences.value[sessionId], title },
  }
  persistConversationPreferences()
  renamingSessionId.value = null
  openSessionMenuId.value = null
}

function cancelRenameConversation(): void {
  renamingSessionId.value = null
  conversationNameDraft.value = ''
}

function toggleConversationPinned(sessionId: string): void {
  const previous = conversationPreferences.value[sessionId] || {}
  const next: ConversationPreference = { ...previous }
  if (previous.pinned) delete next.pinned
  else next.pinned = true
  if (!next.title) delete next.title
  const preferences = { ...conversationPreferences.value }
  if (Object.keys(next).length) preferences[sessionId] = next
  else delete preferences[sessionId]
  conversationPreferences.value = preferences
  persistConversationPreferences()
  openSessionMenuId.value = null
}

async function openConversation(sessionId: string): Promise<void> {
  if (sending.value) return
  openSessionMenuId.value = null
  renamingSessionId.value = null
  activeSessionId.value = sessionId
  messages.value = []
  loading.value = true
  errorMessage.value = ''
  try {
    const history = await getConversationMessages(sessionId)
    messages.value = history.messages
    await scrollToLatest()
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    loading.value = false
  }
}

async function startConversation(): Promise<void> {
  if (sending.value) return
  errorMessage.value = ''
  try {
    const conversation = await createConversation()
    conversations.value = [conversation, ...conversations.value]
    activeSessionId.value = conversation.session_id
    messages.value = []
  } catch (error) {
    errorMessage.value = messageFor(error)
  }
}

async function removeConversation(conversation: ConversationSummary): Promise<void> {
  if (sending.value || deletingSessionId.value) return
  const confirmed = window.confirm(
    `确定删除会话“${conversation.title}”吗？会话消息、检查点、摘要和待审批记忆提案都会删除。`,
  )
  if (!confirmed) return

  deletingSessionId.value = conversation.session_id
  errorMessage.value = ''
  try {
    await deleteConversation(conversation.session_id)
    conversations.value = conversations.value.filter(
      (item) => item.session_id !== conversation.session_id,
    )
    const preferences = { ...conversationPreferences.value }
    delete preferences[conversation.session_id]
    conversationPreferences.value = preferences
    persistConversationPreferences()
    openSessionMenuId.value = null
    if (activeSessionId.value === conversation.session_id) {
      activeSessionId.value = null
      messages.value = []
      const latest = conversations.value[0]
      if (latest) await openConversation(latest.session_id)
    }
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    deletingSessionId.value = null
  }
}

async function upgradeSandbox(targetMode: SandboxMode): Promise<void> {
  const conversation = activeConversation.value
  if (!conversation || upgradingSandbox.value || sending.value) return
  const targetLabel = sandboxModeLabel(targetMode)
  const detail =
    targetMode === 'danger-full-access'
      ? '升级后，本会话的内置文件工具可访问当前操作系统用户有权限访问的任意路径。'
      : '升级后，本会话的内置文件工具可以在当前工作区内写入文件。'
  const confirmed = window.confirm(
    `${detail}\n\n权限只会向上升级，不能在本会话中撤销。确认升级到“${targetLabel}”吗？`,
  )
  if (!confirmed) return

  upgradingSandbox.value = true
  errorMessage.value = ''
  try {
    const upgraded = await upgradeConversationSandbox(conversation.session_id, targetMode)
    conversations.value = conversations.value.map((item) =>
      item.session_id === upgraded.session_id ? upgraded : item,
    )
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    upgradingSandbox.value = false
  }
}

function sandboxModeLabel(mode: SandboxMode): string {
  if (mode === 'workspace-write') return '工作区可写'
  if (mode === 'danger-full-access') return '危险：完全访问'
  return '只读'
}

async function chooseWorkspace(): Promise<void> {
  if (selectingWorkspace.value || sending.value) return
  selectingWorkspace.value = true
  workspaceError.value = ''
  try {
    const selected = await selectWorkspace()
    workspace.value = selected
    if (selected.message) workspaceError.value = selected.message
  } catch (error) {
    workspaceError.value = messageFor(error)
  } finally {
    selectingWorkspace.value = false
  }
}

async function submitMessage(): Promise<void> {
  const message = draft.value.trim()
  if (!message || sending.value) return

  sending.value = true
  errorMessage.value = ''
  messages.value.push({ role: 'user', content: message })
  draft.value = ''
  await scrollToLatest()

  try {
    if (!activeSessionId.value) {
      const conversation = await createConversation(message.slice(0, 40))
      activeSessionId.value = conversation.session_id
      conversations.value = [conversation, ...conversations.value]
    }
    const sessionId = activeSessionId.value
    await chatSocket.send(sessionId, message)
    const history = await getConversationMessages(sessionId)
    messages.value = history.messages
    await refreshConversations()
    await scrollToLatest()
  } catch (error) {
    if (activeSessionId.value) {
      try {
        const history = await getConversationMessages(activeSessionId.value)
        messages.value = history.messages
      } catch {
        messages.value.pop()
      }
    } else {
      messages.value.pop()
    }
    draft.value = message
    errorMessage.value = messageFor(error)
  } finally {
    sending.value = false
  }
}

function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submitMessage()
  }
}

async function useSuggestion(suggestion: string): Promise<void> {
  draft.value = suggestion
  await nextTick()
  composerInput.value?.focus()
}

function respondToApproval(decision: 'approved' | 'rejected' | 'cancelled'): void {
  const request = pendingApproval.value
  if (!request) return
  pendingApproval.value = null
  chatSocket.respondToConfirmation(request.request_id, decision)
}

function conversationTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return date.toLocaleDateString([], { month: 'numeric', day: 'numeric' })
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

async function scrollToLatest(): Promise<void> {
  await nextTick()
  if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
}

watch(visibleMessages, () => void scrollToLatest())

onMounted(async () => {
  loading.value = true
  try {
    await refreshConversations()
    try {
      workspace.value = await getWorkspace()
      if (workspace.value.message) workspaceError.value = workspace.value.message
    } catch (error) {
      workspaceError.value = messageFor(error)
    }
    const latest = conversations.value[0]
    if (latest) await openConversation(latest.session_id)
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    loading.value = false
  }
})

onUnmounted(() => chatSocket.close())
</script>

<template>
  <section
    class="chat-workspace"
    aria-label="聊天工作区"
  >
    <aside class="conversation-sidebar">
      <div class="chat-sidebar-heading">
        <div>
          <span class="chat-kicker">HOSTPILOT</span>
          <h1>工作区</h1>
          <div
            v-if="activeConversation"
            class="sandbox-controls"
            :data-mode="activeConversation.sandbox_mode"
          >
            <span class="sandbox-mode-label">
              本会话：{{ sandboxModeLabel(activeConversation.sandbox_mode) }}
            </span>
            <button
              v-if="activeConversation.sandbox_mode === 'read-only'"
              type="button"
              :disabled="upgradingSandbox || sending"
              @click="upgradeSandbox('workspace-write')"
            >
              升级到工作区可写
            </button>
            <button
              v-if="activeConversation.sandbox_mode !== 'danger-full-access'"
              class="sandbox-danger-upgrade"
              type="button"
              :disabled="upgradingSandbox || sending"
              @click="upgradeSandbox('danger-full-access')"
            >
              升级到完全访问
            </button>
          </div>
        </div>
        <div class="chat-sidebar-actions">
          <button
            class="workspace-select-button"
            type="button"
            :disabled="selectingWorkspace || sending"
            @click="chooseWorkspace"
          >
            {{ selectingWorkspace ? '正在选择…' : '选择目录' }}
          </button>
          <button
            class="new-chat-button"
            type="button"
            :disabled="sending"
            @click="startConversation"
          >
            <span aria-hidden="true">＋</span>新建
          </button>
        </div>
      </div>
      <div
        v-if="workspace"
        class="workspace-summary"
        aria-label="当前工作目录"
      >
        <span class="workspace-summary-label">当前目录</span>
        <p
          class="workspace-path"
          :title="workspace.path"
        >
          {{ workspace.path }}
        </p>
        <span :class="['workspace-access', workspace.read_only ? 'workspace-access-readonly' : '']">
          {{ workspace.read_only ? '只读访问' : '可写访问' }}
        </span>
      </div>
      <p
        v-if="workspaceError"
        class="workspace-error"
        role="alert"
      >
        {{ workspaceError }}
      </p>
      <div
        class="conversation-list"
        aria-label="会话列表"
      >
        <p class="conversation-list-label">
          最近对话
        </p>
        <input
          v-model="conversationQuery"
          class="conversation-search"
          type="search"
          aria-label="搜索会话"
          placeholder="搜索会话标题"
        >
        <section
          v-for="group in conversationGroups"
          :key="group.label"
          class="conversation-date-group"
        >
          <h2>{{ group.label }}</h2>
          <div
            v-for="conversation in group.items"
            :key="conversation.session_id"
            class="conversation-row"
          >
            <button
              class="conversation-item"
              :class="{ 'conversation-item-active': activeSessionId === conversation.session_id }"
              type="button"
              @click="openConversation(conversation.session_id)"
            >
              <span
                class="conversation-icon"
                aria-hidden="true"
              >
                <svg
                  viewBox="0 0 20 20"
                  fill="none"
                >
                  <path
                    d="M4 4.75h12M4 8.5h12M4 12.25h7"
                    stroke="currentColor"
                    stroke-width="1.4"
                    stroke-linecap="round"
                  />
                  <path
                    d="M3 2.75h14a1 1 0 0 1 1 1v10.5a1 1 0 0 1-1 1h-8l-4.5 2v-2H3a1 1 0 0 1-1-1V3.75a1 1 0 0 1 1-1Z"
                    stroke="currentColor"
                    stroke-width="1.3"
                    stroke-linejoin="round"
                  />
                </svg>
              </span>
              <span class="conversation-copy">
                <span class="conversation-title">{{ conversation.title }}</span>
                <small>{{ conversationTime(conversation.updated_at) }} · {{ sandboxModeLabel(conversation.sandbox_mode) }}</small>
              </span>
            </button>
            <button
              class="conversation-more-button"
              type="button"
              :aria-label="`会话操作 ${conversation.title}`"
              :aria-expanded="openSessionMenuId === conversation.session_id"
              title="会话操作"
              @click.stop="toggleConversationMenu(conversation.session_id)"
            >
              <span aria-hidden="true">⋯</span>
            </button>
            <div
              v-if="openSessionMenuId === conversation.session_id"
              class="conversation-actions-menu"
              role="group"
              :aria-label="`${conversation.title} 的操作`"
              @keydown.esc.stop.prevent="openSessionMenuId = null"
            >
              <form
                v-if="renamingSessionId === conversation.session_id"
                class="conversation-rename-form"
                @submit.prevent="saveConversationName(conversation.session_id)"
              >
                <label :for="`conversation-name-${conversation.session_id}`">会话名称</label>
                <input
                  :id="`conversation-name-${conversation.session_id}`"
                  ref="conversationNameInput"
                  v-model="conversationNameDraft"
                  aria-label="会话名称"
                  maxlength="200"
                  required
                >
                <div>
                  <button type="submit">
                    保存名称
                  </button>
                  <button
                    type="button"
                    @click="cancelRenameConversation"
                  >
                    取消
                  </button>
                </div>
              </form>
              <template v-else>
                <button
                  type="button"
                  aria-label="重命名会话"
                  @click="beginRenameConversation(conversation)"
                >
                  重命名
                </button>
                <button
                  type="button"
                  :aria-label="conversation.pinned ? '取消置顶会话' : '置顶会话'"
                  @click="toggleConversationPinned(conversation.session_id)"
                >
                  {{ conversation.pinned ? '取消置顶' : '置顶' }}
                </button>
                <button
                  type="button"
                  class="conversation-action-delete"
                  :disabled="sending || deletingSessionId !== null"
                  :aria-label="`删除会话 ${conversation.title}`"
                  @click="removeConversation(conversation)"
                >
                  删除
                </button>
              </template>
            </div>
          </div>
        </section>
        <p
          v-if="conversations.length && !filteredConversations.length"
          class="conversation-empty"
        >
          没有匹配的会话。
        </p>
        <p
          v-if="!conversations.length && !loading"
          class="conversation-empty"
        >
          还没有对话，发送一条消息开始交流。
        </p>
      </div>
      <p class="chat-sidebar-note">
        <span class="local-indicator" />会话数据仅保存在本机
      </p>
    </aside>

    <div class="chat-main">
      <header class="chat-header">
        <div class="chat-heading-copy">
          <span class="chat-kicker">PERSONAL AI WORKSPACE</span>
          <h2>{{ activeConversation?.title || '新的对话' }}</h2>
        </div>
        <div class="chat-header-meta">
          <span class="chat-mode"><i />模型对话</span>
          <span class="chat-model-caption">由当前配置的模型提供支持</span>
        </div>
      </header>

      <div
        ref="messageList"
        class="message-list"
        aria-live="polite"
        :aria-busy="loading || sending"
      >
        <div
          v-if="!visibleMessages.length && !loading"
          class="chat-welcome"
        >
          <span
            class="welcome-mark"
            aria-hidden="true"
          >
            <svg
              viewBox="0 0 48 48"
              fill="none"
            >
              <path
                d="M24 4.5 28.4 18l13.6 6-13.6 5.1L24 43.5l-5-14.4L5.5 24l13.5-6L24 4.5Z"
                fill="currentColor"
              />
              <path
                d="m38 4 1.5 4.5L44 10l-4.5 1.5L38 16l-1.5-4.5L32 10l4.5-1.5L38 4Z"
                fill="currentColor"
                opacity=".52"
              />
            </svg>
          </span>
          <span class="chat-welcome-kicker">你好，欢迎使用 HostPilot</span>
          <h3>今天想一起完成什么？</h3>
          <p>从一个问题开始。你的对话会保存在本地，并由已配置的模型继续处理。</p>
          <div
            class="suggestion-list"
            aria-label="对话建议"
          >
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              type="button"
              @click="useSuggestion(suggestion)"
            >
              <span aria-hidden="true">↗</span>{{ suggestion }}
            </button>
          </div>
        </div>
        <div
          v-if="loading && !visibleMessages.length"
          class="chat-loading-state"
        >
          <span class="loading-orbit" />正在加载对话
        </div>
        <article
          v-for="(message, index) in visibleMessages"
          :key="`${index}-${message.role}`"
          class="message-row"
          :class="`message-${message.role}`"
        >
          <span
            class="message-avatar"
            aria-hidden="true"
          >
            {{ message.role === 'user' ? '你' : 'H' }}
          </span>
          <div class="message-content">
            <span class="message-author">{{ message.role === 'user' ? '你' : 'HostPilot' }}</span>
            <p>{{ message.content }}</p>
          </div>
        </article>
        <article
          v-if="sending"
          class="message-row message-assistant"
        >
          <span
            class="message-avatar"
            aria-hidden="true"
          >H</span>
          <div class="message-content">
            <span class="message-author">HostPilot</span>
            <p class="typing-indicator">
              正在思考…
            </p>
          </div>
        </article>
      </div>

      <div class="composer-area">
        <p
          v-if="errorMessage"
          class="chat-error"
          role="alert"
        >
          {{ errorMessage }}
        </p>
        <form
          class="chat-composer"
          @submit.prevent="submitMessage"
        >
          <textarea
            ref="composerInput"
            v-model="draft"
            aria-label="输入消息"
            :disabled="sending"
            placeholder="写下你的问题或想法…"
            rows="3"
            @keydown="handleComposerKeydown"
          />
          <div class="composer-footer">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <button
              type="submit"
              :disabled="!draft.trim() || sending"
            >
              {{ sending ? '发送中…' : '发送' }} <span aria-hidden="true">↗</span>
            </button>
          </div>
        </form>
      </div>
    </div>

    <div
      v-if="pendingApproval"
      class="tool-approval-backdrop"
    >
      <section
        class="tool-approval-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tool-approval-title"
      >
        <div
          class="approval-icon"
          aria-hidden="true"
        >
          !
        </div>
        <span class="chat-kicker">HUMAN APPROVAL REQUIRED</span>
        <h2 id="tool-approval-title">
          确认工具操作
        </h2>
        <p class="approval-tool-name">
          {{ pendingApproval.tool_name }}
        </p>
        <p class="approval-summary">
          {{ pendingApproval.summary }}
        </p>
        <p class="approval-note">
          批准后才会执行此写入操作；拒绝后文件不会改变。
        </p>
        <footer class="approval-actions">
          <button
            type="button"
            class="approval-cancel"
            @click="respondToApproval('cancelled')"
          >
            取消本次请求
          </button>
          <button
            type="button"
            class="approval-reject"
            @click="respondToApproval('rejected')"
          >
            拒绝
          </button>
          <button
            type="button"
            class="approval-accept"
            @click="respondToApproval('approved')"
          >
            批准并执行
          </button>
        </footer>
      </section>
    </div>
  </section>
</template>
