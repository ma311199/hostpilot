<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  createConversation,
  getConversationMessages,
  listConversations,
  type ConversationMessage,
  type ConversationSummary,
} from './api/chat'
import { ChatSocketClient, type ToolConfirmationMessage } from './api/chatSocket'

const conversations = ref<ConversationSummary[]>([])
const activeConversationId = ref<string | null>(null)
const messages = ref<ConversationMessage[]>([])
const draft = ref('')
const loading = ref(false)
const sending = ref(false)
const errorMessage = ref('')
const messageList = ref<HTMLElement | null>(null)
const composerInput = ref<HTMLTextAreaElement | null>(null)
const chatSocket = new ChatSocketClient()
const pendingApproval = ref<ToolConfirmationMessage | null>(null)
const visibleMessages = computed(() =>
  messages.value.filter(
    (message) =>
      (message.role === 'user' || message.role === 'assistant') && message.content.trim().length > 0,
  ),
)
const activeConversation = computed(() =>
  conversations.value.find((conversation) => conversation.conversation_id === activeConversationId.value),
)
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

async function openConversation(conversationId: string): Promise<void> {
  if (sending.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const history = await getConversationMessages(conversationId)
    activeConversationId.value = conversationId
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
    activeConversationId.value = conversation.conversation_id
    messages.value = []
  } catch (error) {
    errorMessage.value = messageFor(error)
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
    if (!activeConversationId.value) {
      const conversation = await createConversation(message.slice(0, 40))
      activeConversationId.value = conversation.conversation_id
      conversations.value = [conversation, ...conversations.value]
    }
    const conversationId = activeConversationId.value
    await chatSocket.send(conversationId, message)
    const history = await getConversationMessages(conversationId)
    messages.value = history.messages
    await refreshConversations()
    await scrollToLatest()
  } catch (error) {
    if (activeConversationId.value) {
      try {
        const history = await getConversationMessages(activeConversationId.value)
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
    const latest = conversations.value[0]
    if (latest) await openConversation(latest.conversation_id)
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
        </div>
        <button
          class="new-chat-button"
          type="button"
          :disabled="sending"
          @click="startConversation"
        >
          <span aria-hidden="true">＋</span>新建
        </button>
      </div>
      <div
        class="conversation-list"
        aria-label="会话列表"
      >
        <p class="conversation-list-label">
          最近对话
        </p>
        <button
          v-for="conversation in conversations"
          :key="conversation.conversation_id"
          class="conversation-item"
          :class="{ 'conversation-item-active': activeConversationId === conversation.conversation_id }"
          type="button"
          @click="openConversation(conversation.conversation_id)"
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
            <small>{{ conversationTime(conversation.updated_at) }}</small>
          </span>
        </button>
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
