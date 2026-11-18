<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { getTrace, getTraces, type TraceDetail, type TraceSummary } from './api/traces'
import PaginationControls from './PaginationControls.vue'

const traces = ref<TraceSummary[]>([])
const selectedTraceId = ref('')
const detail = ref<TraceDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const errorMessage = ref('')
const detailError = ref('')
const query = ref('')
const statusFilter = ref('all')
const page = ref(1)
const pageSize = 12
let detailRequest = 0
const visibleTraces = computed(() => traces.value.filter((trace) => {
  const search = query.value.trim().toLocaleLowerCase()
  const matchesSearch = !search || `${trace.trace_id} ${trace.skill_id || ''} ${trace.last_event} ${trace.status || ''}`.toLocaleLowerCase().includes(search)
  const matchesStatus = statusFilter.value === 'all' || trace.status === statusFilter.value
  return matchesSearch && matchesStatus
}))
const pageCount = computed(() => Math.max(1, Math.ceil(visibleTraces.value.length / pageSize)))
const pagedTraces = computed(() => visibleTraces.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([query, statusFilter], () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })
watch(pagedTraces, (items) => {
  if (selectedTraceId.value && !items.some((trace) => trace.trace_id === selectedTraceId.value)) {
    selectedTraceId.value = ''
    detail.value = null
    detailRequest += 1
  }
})

async function refresh(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    traces.value = await getTraces()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取 Trace 列表失败。'
  } finally {
    loading.value = false
  }
}

async function showTrace(traceId: string): Promise<void> {
  selectedTraceId.value = traceId
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  const request = ++detailRequest
  try {
    const result = await getTrace(traceId)
    if (request === detailRequest) detail.value = result
  } catch (error) {
    if (request === detailRequest) {
      detailError.value = error instanceof Error ? error.message : '读取 Trace 详情失败。'
    }
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function attributeLabel(key: string): string {
  const labels: Record<string, string> = {
    status: '状态',
    reason_code: '原因类型',
    chunk_count: '记忆片段数',
    character_count: '字符数',
    skill_id: 'Skill ID',
    tool_id: '工具 ID',
    execution_mode: '执行入口',
    duration_ms: '耗时（毫秒）',
    route: '路由',
    total_tokens: 'Token 数',
  }
  return labels[key] || key
}

onMounted(() => void refresh())
</script>

<template>
  <section
    class="traces-page"
    aria-label="Trace 观测"
  >
    <header class="traces-header">
      <div>
        <span class="traces-kicker">HARNESS OBSERVABILITY</span>
        <h1>Trace 观测</h1>
        <p>按请求查看 Skill、工具、审批、路由和记忆事件的 Span 关系；不保存对话正文、审批摘要或工具参数。</p>
      </div>
      <button
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '正在刷新…' : '刷新 Trace' }}
      </button>
    </header>

    <p
      v-if="errorMessage"
      class="traces-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <div
      v-if="traces.length"
      class="traces-browse-controls"
    >
      <label>搜索记录<input
        v-model="query"
        type="search"
        aria-label="搜索记录"
        placeholder="Trace、Skill、事件或状态"
      ></label>
      <label>状态<select v-model="statusFilter"><option value="all">全部状态</option><option
        v-for="status in [...new Set(traces.map((trace) => trace.status).filter((value): value is string => Boolean(value)))]"
        :key="status"
        :value="status"
      >{{ status }}</option></select></label>
      <span>{{ visibleTraces.length }} 条记录</span>
    </div>
    <div
      v-if="loading && !traces.length"
      class="traces-empty"
      role="status"
    >
      正在读取 Trace…
    </div>
    <div
      v-else-if="!traces.length && !errorMessage"
      class="traces-empty"
    >
      暂无 Trace。完成聊天请求后，这里会显示请求路由和已记录的运行事件。
    </div>
    <div
      v-else-if="visibleTraces.length"
      class="traces-layout"
    >
      <div
        class="traces-list"
        aria-label="最近请求"
      >
        <button
          v-for="trace in pagedTraces"
          :key="trace.trace_id"
          type="button"
          :class="['trace-card', { 'trace-card-active': selectedTraceId === trace.trace_id }]"
          @click="showTrace(trace.trace_id)"
        >
          <span class="trace-card-time">{{ formatTime(trace.last_occurred_at) }}</span>
          <strong>{{ trace.skill_id || trace.last_event }}</strong>
          <span>{{ trace.status || '运行记录' }} · {{ trace.event_count }} 个事件</span>
          <code>{{ trace.trace_id }}</code>
        </button>
      </div>

      <section
        v-if="selectedTraceId"
        class="trace-detail"
        aria-label="Trace 详情"
      >
        <h2>请求事件</h2>
        <code class="trace-detail-id">{{ selectedTraceId }}</code>
        <p
          v-if="detailLoading"
          role="status"
        >
          正在读取事件…
        </p>
        <p
          v-else-if="detailError"
          class="traces-error"
          role="alert"
        >
          {{ detailError }}
        </p>
        <ol
          v-else-if="detail"
          class="trace-event-list"
        >
          <li
            v-for="event in detail.events"
            :key="event.event_id"
          >
            <div class="trace-event-heading">
              <strong>{{ event.name }}</strong>
              <time>{{ formatTime(event.occurred_at) }}</time>
            </div>
            <div class="trace-event-span">
              <span>Span</span>
              <code>{{ event.span_id }}</code>
              <template v-if="event.parent_span_id">
                <span>父级</span>
                <code>{{ event.parent_span_id }}</code>
              </template>
            </div>
            <dl v-if="Object.keys(event.attributes).length">
              <template
                v-for="(value, key) in event.attributes"
                :key="key"
              >
                <dt>{{ attributeLabel(key) }}</dt>
                <dd>{{ value }}</dd>
              </template>
            </dl>
          </li>
        </ol>
      </section>
    </div>
    <div
      v-if="traces.length && !visibleTraces.length"
      class="traces-empty"
    >
      没有符合条件的 Trace。
    </div>
    <PaginationControls
      v-model:page="page"
      :page-size="pageSize"
      :total="visibleTraces.length"
    />
    <p class="traces-note">
      本地 Trace 元数据最多保留 30 天或 100,000 个事件。事件只写入明确允许的字段；不记录对话正文、工具参数或审批摘要。
    </p>
  </section>
</template>

<style scoped>
.traces-page {
  width: min(100% - 40px, 1280px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.traces-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.traces-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.traces-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.traces-header p,
.traces-note {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.traces-browse-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 18px 0; }
.traces-browse-controls label { display: grid; gap: 5px; color: #52665a; font-size: 12px; }
.traces-browse-controls input, .traces-browse-controls select { min-width: 180px; min-height: 42px; padding: 0 10px; border: 1px solid #dce4dc; border-radius: 8px; background: white; }
.traces-browse-controls > span { padding: 0 0 12px; color: #718076; font-size: 12px; }
.traces-list { max-height: 70vh; overflow: auto; overscroll-behavior: contain; }
.trace-detail { max-height: 70vh; overflow: auto; overscroll-behavior: contain; }

.traces-page button {
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  cursor: pointer;
}

.traces-header button {
  min-height: 38px;
  padding: 0 14px;
  white-space: nowrap;
}

.traces-page button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.traces-layout {
  display: grid;
  grid-template-columns: minmax(250px, 0.8fr) minmax(0, 1.2fr);
  align-items: start;
  gap: 16px;
}

.traces-list {
  display: grid;
  gap: 8px;
}

.trace-card {
  display: grid;
  gap: 8px;
  width: 100%;
  padding: 15px;
  text-align: left;
}

.trace-card-active {
  border-color: #78977f !important;
  background: #f5f8f5 !important;
}

.trace-card-time,
.trace-card > span,
.trace-detail-id,
.trace-event-heading time {
  color: #718076;
  font-size: 11px;
}

.trace-event-span {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 8px;
  margin-top: 7px;
  color: #849087;
  font-size: 10px;
}

.trace-event-span code {
  max-width: 100%;
  overflow-wrap: anywhere;
  color: #718076;
  font-family: var(--mono);
}

.trace-card code,
.trace-detail-id {
  overflow-wrap: anywhere;
  font-family: var(--mono);
}

.trace-detail {
  padding: 18px;
  border: 1px solid #e2e9e2;
  border-radius: 12px;
  background: #fff;
}

.trace-detail h2 {
  margin-bottom: 8px;
  font-size: 18px;
}

.trace-event-list {
  display: grid;
  gap: 12px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.trace-event-list li {
  padding: 14px;
  border: 1px solid #edf1ed;
  border-radius: 9px;
}

.trace-event-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.trace-event-heading strong {
  font-family: var(--mono);
  font-size: 12px;
}

.trace-event-list dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  margin: 13px 0 0;
  font-size: 11px;
}

.trace-event-list dt {
  color: #718076;
}

.trace-event-list dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.traces-empty {
  padding: 28px;
  border: 1px dashed #dce4dc;
  border-radius: 12px;
  color: #718076;
  text-align: center;
  font-size: 13px;
}

.traces-error {
  color: #a5443a;
  font-size: 12px;
}

.traces-note {
  margin-top: 20px;
  font-size: 11px;
}

@media (max-width: 720px) {
  .traces-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .traces-layout {
    grid-template-columns: 1fr;
  }
  .traces-list, .trace-detail { max-height: none; }
}
</style>
