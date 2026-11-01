<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import ChatPanel from './ChatPanel.vue'
import ModelSettings from './ModelSettings.vue'
import ToolsPanel from './ToolsPanel.vue'
import { createPingMessage, getStatusSocketUrl, parseStatusMessage } from './api/statusSocket'
import { latestQualityCheck, projectPhases } from './data/roadmap'

type ConnectionState = 'connecting' | 'online' | 'offline'

const connectionState = ref<ConnectionState>('connecting')
const activeView = ref<'dashboard' | 'chat' | 'tools'>('dashboard')
const showModelSettings = ref(false)
const lastSeen = ref<Date | null>(null)
const activePhase = computed(() => projectPhases.find((phase) => phase.status === 'active'))
const completedPhases = computed(
  () => projectPhases.filter((phase) => phase.status === 'complete').length,
)

let socket: WebSocket | null = null
let heartbeatTimer: number | undefined
let reconnectTimer: number | undefined
let reconnectAttempt = 0
let pingSequence = 0
let lastResponseAt = 0
let disposed = false

function connect(): void {
  if (disposed) return
  connectionState.value = 'connecting'

  try {
    socket = new WebSocket(getStatusSocketUrl())
  } catch {
    connectionState.value = 'offline'
    scheduleReconnect()
    return
  }

  const currentSocket = socket
  currentSocket.onopen = () => {
    reconnectAttempt = 0
    lastResponseAt = Date.now()
    sendPing()
    heartbeatTimer = window.setInterval(() => {
      if (Date.now() - lastResponseAt > 45_000) {
        currentSocket.close()
        return
      }
      sendPing()
    }, 15_000)
  }

  currentSocket.onmessage = (event: MessageEvent<string>) => {
    const message = parseStatusMessage(String(event.data))
    if (message?.type === 'status.snapshot' || message?.type === 'pong') {
      lastResponseAt = Date.now()
      lastSeen.value = new Date(lastResponseAt)
      connectionState.value = 'online'
    } else if (message?.type === 'error') {
      connectionState.value = 'offline'
    }
  }

  currentSocket.onerror = () => currentSocket.close()
  currentSocket.onclose = () => {
    if (socket === currentSocket) socket = null
    clearHeartbeat()
    connectionState.value = 'offline'
    scheduleReconnect()
  }
}

function sendPing(): void {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(createPingMessage(++pingSequence))
  }
}

function scheduleReconnect(): void {
  if (disposed || reconnectTimer !== undefined) return
  const delay = Math.min(1_000 * 2 ** reconnectAttempt, 30_000)
  reconnectAttempt += 1
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined
    connect()
  }, delay)
}

function clearHeartbeat(): void {
  if (heartbeatTimer !== undefined) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = undefined
  }
}

function statusLabel(status: ConnectionState): string {
  if (status === 'online') return '后端在线'
  if (status === 'connecting') return '正在连接'
  return '后端离线'
}

function phaseStatusLabel(status: string): string {
  if (status === 'complete') return '已完成首版'
  if (status === 'active') return '进行中'
  return '计划中'
}

onMounted(connect)

onUnmounted(() => {
  disposed = true
  clearHeartbeat()
  if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
  socket?.close()
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a
        class="brand"
        href="#top"
        aria-label="HostPilot 首页"
      >
        <span class="brand-mark">H</span>
        <span>hostpilot<span class="brand-period">.</span></span>
      </a>
      <nav
        class="topnav"
        aria-label="主导航"
      >
        <button
          :class="{ 'nav-active': activeView === 'dashboard' }"
          type="button"
          @click="activeView = 'dashboard'"
        >
          项目路线
        </button>
        <button
          :class="{ 'nav-active': activeView === 'chat' }"
          type="button"
          @click="activeView = 'chat'"
        >
          聊天
        </button>
        <button
          :class="{ 'nav-active': activeView === 'tools' }"
          type="button"
          @click="activeView = 'tools'"
        >
          工具
        </button>
        <a
          v-if="activeView === 'dashboard'"
          href="#next"
        >下一步</a>
      </nav>
      <div
        class="connection-pill"
        :class="'connection-' + connectionState"
        role="status"
      >
        <span
          class="status-dot"
          aria-hidden="true"
        />
        {{ statusLabel(connectionState) }}
      </div>
      <button
        class="model-config-button"
        type="button"
        @click="showModelSettings = true"
      >
        模型配置
      </button>
    </header>

    <main id="top">
      <ChatPanel v-if="activeView === 'chat'" />
      <ToolsPanel v-else-if="activeView === 'tools'" />
      <template v-else>
        <section class="hero">
          <div class="hero-copy">
            <div class="eyebrow">
              <span class="eyebrow-line" />工程进度 / 2026
            </div>
            <h1>HostPilot<br><span>开发进度</span></h1>
            <p class="hero-description">
              一个面向本地个人使用、可治理且可观测的 Agent 后端。查看当前交付、阶段边界与接下来要完成的工作。
            </p>
            <a
              class="hero-link"
              href="#roadmap"
            >查看项目路线 <span aria-hidden="true">↓</span></a>
          </div>

          <aside
            class="hero-panel"
            aria-label="当前项目状态"
          >
            <div class="panel-topline">
              <span>当前阶段</span>
              <span class="live-mark"><i /> LIVE</span>
            </div>
            <div class="phase-number">
              01<span>/03</span>
            </div>
            <h2>{{ activePhase?.title }}</h2>
            <p>{{ activePhase?.summary }}</p>
            <div class="panel-divider" />
            <div class="panel-meta">
              <span>已完成阶段</span>
              <strong>{{ completedPhases }} / {{ projectPhases.length }}</strong>
            </div>
            <div class="panel-meta">
              <span>后端 API</span>
              <strong>{{ statusLabel(connectionState) }}</strong>
            </div>
            <div class="panel-footnote">
              <span class="pulse-dot" />
              <span v-if="lastSeen">最近响应 {{ lastSeen.toLocaleTimeString() }}</span>
              <span v-else>等待状态快照</span>
            </div>
          </aside>
        </section>

        <section
          class="quality-strip"
          aria-label="最近质量检查"
        >
          <div class="quality-label">
            <span class="check-icon">✓</span>后端质量门
          </div>
          <div class="quality-stat">
            <strong>{{ latestQualityCheck.testsPassed }}</strong> 项测试通过
          </div>
          <div class="quality-summary">
            {{ latestQualityCheck.summary }}
          </div>
          <div class="quality-date">
            {{ latestQualityCheck.date }}
          </div>
        </section>

        <section
          id="roadmap"
          class="roadmap-section"
        >
          <div class="section-heading">
            <div>
              <div class="eyebrow">
                <span class="eyebrow-line" />ROADMAP
              </div>
              <h2>阶段路线</h2>
            </div>
            <p>阶段状态依据仓库路线文档维护；服务在线状态通过 WebSocket 实时检查。</p>
          </div>

          <div class="phase-grid">
            <article
              v-for="phase in projectPhases"
              :key="phase.id"
              class="phase-card"
              :class="'phase-card-' + phase.status"
            >
              <div class="phase-card-head">
                <span class="phase-name">{{ phase.name }}</span>
                <span
                  class="phase-badge"
                  :class="'badge-' + phase.status"
                >
                  <i />{{ phaseStatusLabel(phase.status) }}
                </span>
              </div>
              <h3>{{ phase.title }}</h3>
              <p class="phase-summary">
                {{ phase.summary }}
              </p>
              <div
                v-if="phase.delivered.length"
                class="phase-list-group"
              >
                <div class="list-label">
                  已交付
                </div>
                <ul>
                  <li
                    v-for="item in phase.delivered"
                    :key="item"
                  >
                    <span class="list-check">✓</span>{{ item }}
                  </li>
                </ul>
              </div>
              <div
                v-if="phase.next.length"
                class="phase-list-group phase-next-list"
              >
                <div class="list-label">
                  {{ phase.status === 'active' ? '待完成' : '计划内容' }}
                </div>
                <ul>
                  <li
                    v-for="item in phase.next"
                    :key="item"
                  >
                    <span class="list-arrow">↗</span>{{ item }}
                  </li>
                </ul>
              </div>
              <div
                v-if="!phase.delivered.length && !phase.next.length"
                class="phase-empty"
              >
                阶段交付已记录在项目路线文档中
              </div>
            </article>
          </div>
        </section>

        <section
          id="next"
          class="next-section"
        >
          <div class="next-heading">
            <div class="eyebrow">
              <span class="eyebrow-line" />NEXT UP
            </div>
            <h2>接下来要打通的路径</h2>
          </div>
          <div class="next-grid">
            <article class="next-card">
              <span class="next-index">01</span>
              <div>
                <h3>完善路由决策</h3>
                <p>让 Router 能按意图选择 direct、clarify 和受治理的工具调用路径。</p>
              </div>
              <span
                class="next-arrow"
                aria-hidden="true"
              >↗</span>
            </article>
            <article class="next-card">
              <span class="next-index">02</span>
              <div>
                <h3>打通顶层工具路由</h3>
                <p>让 ChatService 支持 Router 选出的 tool_call 决策，并保留现有策略治理。</p>
              </div>
              <span
                class="next-arrow"
                aria-hidden="true"
              >↗</span>
            </article>
            <article class="next-card">
              <span class="next-index">03</span>
              <div>
                <h3>启用知识库检索</h3>
                <p>确认外部服务契约后配置 RAG_KEY，并恢复 EvidenceSet 校验。</p>
              </div>
              <span
                class="next-arrow"
                aria-hidden="true"
              >↗</span>
            </article>
          </div>
        </section>
      </template>
    </main>

    <ModelSettings
      v-if="showModelSettings"
      @close="showModelSettings = false"
    />

    <footer class="footer">
      <span>HOSTPILOT / LOCAL AGENT BACKEND</span>
      <span>阶段进度需与 docs/roadmap.md 同步维护</span>
    </footer>
  </div>
</template>
