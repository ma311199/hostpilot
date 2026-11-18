<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { getAuditEvents, getAuditExportUrl, type AuditEvent } from './api/audit'

const events = ref<AuditEvent[]>([])
const traceId = ref('')
const since = ref('')
const until = ref('')
const offset = ref(0)
const pageSize = 100
const loading = ref(false)
const errorMessage = ref('')
const filterError = ref('')
let requestSequence = 0

async function refresh(): Promise<void> {
  filterError.value = filterValidationMessage()
  if (filterError.value) return
  const request = ++requestSequence
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await getAuditEvents({
      limit: pageSize,
      offset: offset.value,
      traceId: traceId.value,
      since: toIsoTime(since.value),
      until: toIsoTime(until.value),
    })
    if (request === requestSequence) events.value = result
  } catch (error) {
    if (request === requestSequence) {
      errorMessage.value = error instanceof Error ? error.message : '读取 Audit 事件失败。'
    }
  } finally {
    if (request === requestSequence) loading.value = false
  }
}

function applyFilters(): void {
  filterError.value = filterValidationMessage()
  if (filterError.value) return
  offset.value = 0
  void refresh()
}

function clearFilters(): void {
  traceId.value = ''
  since.value = ''
  until.value = ''
  filterError.value = ''
  applyFilters()
}

function previousPage(): void {
  offset.value = Math.max(0, offset.value - pageSize)
  void refresh()
}

function nextPage(): void {
  if (events.value.length < pageSize) return
  offset.value += pageSize
  void refresh()
}

const exportUrl = computed(() => filterValidationMessage()
  ? undefined
  : getAuditExportUrl({
      traceId: traceId.value,
      since: toIsoTime(since.value),
      until: toIsoTime(until.value),
    }))

function filterValidationMessage(): string {
  const start = since.value ? new Date(since.value) : null
  const end = until.value ? new Date(until.value) : null
  if (start && Number.isNaN(start.getTime())) return '开始时间无效，请重新选择。'
  if (end && Number.isNaN(end.getTime())) return '结束时间无效，请重新选择。'
  if (start && end && start > end) return '开始时间不能晚于结束时间。'
  return ''
}

function toIsoTime(value: string): string | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function outcomeLabel(outcome: AuditEvent['outcome']): string {
  const labels: Record<AuditEvent['outcome'], string> = {
    allowed: '已允许',
    denied: '已拒绝',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return labels[outcome]
}

onMounted(() => void refresh())
</script>

<template>
  <section
    class="audit-page"
    aria-label="Audit 审计"
  >
    <header class="audit-header">
      <div>
        <span class="audit-kicker">HARNESS GOVERNANCE</span>
        <h1>Audit 审计</h1>
        <p>查询工具和记忆审批的安全决策记录。这里只展示审计元数据，不包含工具参数、对话正文或凭证。</p>
      </div>
      <button
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '正在查询…' : '刷新记录' }}
      </button>
    </header>

    <form
      class="audit-filters"
      aria-label="Audit 筛选条件"
      @submit.prevent="applyFilters"
    >
      <label>
        Trace ID
        <input
          v-model="traceId"
          type="text"
          placeholder="可选，按请求关联"
        >
      </label>
      <label>
        开始时间
        <input
          v-model="since"
          type="datetime-local"
          @input="filterError = ''"
        >
      </label>
      <label>
        结束时间
        <input
          v-model="until"
          type="datetime-local"
          @input="filterError = ''"
        >
      </label>
      <button
        type="submit"
        :disabled="loading"
      >
        筛选
      </button>
      <a
        v-if="exportUrl"
        class="audit-export-button"
        :href="exportUrl"
        download="hostpilot-audit.csv"
      >
        导出 CSV
      </a>
      <button
        type="button"
        class="audit-clear-button"
        :disabled="loading || (!traceId && !since && !until)"
        @click="clearFilters"
      >
        清除
      </button>
    </form>
    <p
      v-if="filterError"
      class="audit-error"
      role="alert"
    >
      {{ filterError }}
    </p>
    <p class="audit-filter-note">
      时间按浏览器本地时区解释；每页 100 条，CSV 最多导出 1,000 条匹配记录。
    </p>

    <p
      v-if="errorMessage"
      class="audit-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <div
      v-if="loading && !events.length"
      class="audit-empty"
      role="status"
    >
      正在读取审计记录…
    </div>
    <div
      v-else-if="!events.length && !errorMessage"
      class="audit-empty"
    >
      {{ offset > 0 ? '当前页没有匹配记录，请返回上一页。' : '暂无审计记录。工具调用或记忆审批产生记录后会显示在这里。' }}
    </div>
    <div
      v-else-if="events.length"
      class="audit-table-wrap"
    >
      <table class="audit-table">
        <thead>
          <tr>
            <th scope="col">
              时间
            </th>
            <th scope="col">
              操作
            </th>
            <th scope="col">
              结果
            </th>
            <th scope="col">
              资源
            </th>
            <th scope="col">
              原因
            </th>
            <th scope="col">
              Trace ID
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="event in events"
            :key="event.event_id"
          >
            <td><time>{{ formatTime(event.occurred_at) }}</time></td>
            <td><code>{{ event.action }}</code></td>
            <td><span :class="['audit-outcome', `audit-outcome-${event.outcome}`]">{{ outcomeLabel(event.outcome) }}</span></td>
            <td>
              <span>{{ event.resource_type }}</span>
              <code v-if="event.resource_id">{{ event.resource_id }}</code>
            </td>
            <td>{{ event.reason_code || '—' }}</td>
            <td><code>{{ event.trace_id || '—' }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
    <nav
      v-if="events.length || offset > 0"
      class="audit-pagination"
      aria-label="Audit 分页"
    >
      <span>第 {{ Math.floor(offset / pageSize) + 1 }} 页</span>
      <button
        type="button"
        :disabled="loading || offset === 0"
        @click="previousPage"
      >
        上一页
      </button>
      <button
        type="button"
        :disabled="loading || events.length < pageSize"
        @click="nextPage"
      >
        下一页
      </button>
    </nav>
    <p class="audit-retention-note">
      Audit 元数据最多保留 90 天或 100,000 条事件。此页面是只读视图。
    </p>
  </section>
</template>

<style scoped>
.audit-page {
  width: min(100% - 40px, 1120px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.audit-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.audit-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.audit-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.audit-header p,
.audit-filter-note,
.audit-retention-note {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.audit-page button {
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  cursor: pointer;
  white-space: nowrap;
}

.audit-page button:disabled {
  cursor: wait;
  opacity: 0.6;
}

.audit-export-button {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  font-size: 12px;
  text-decoration: none;
  white-space: nowrap;
}

.audit-filters {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 16px;
  border: 1px solid #e2e8e2;
  border-radius: 12px;
  background: #fbfcf9;
}

.audit-filters label {
  display: grid;
  flex: 1;
  gap: 7px;
  color: #637368;
  font-size: 12px;
}

.audit-filters input {
  width: 100%;
  min-height: 38px;
  padding: 0 10px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  background: #fff;
  color: #263b30;
  font: inherit;
}

.audit-page .audit-clear-button {
  background: transparent;
}

.audit-filter-note {
  margin: 8px 0 18px;
  font-size: 11px;
}

.audit-error {
  color: #a54338;
}

.audit-empty {
  padding: 34px;
  border: 1px dashed #dce4dc;
  border-radius: 12px;
  color: #718076;
  text-align: center;
}

.audit-table-wrap {
  overflow-x: auto;
  border: 1px solid #e2e8e2;
  border-radius: 12px;
}

.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}

.audit-table th,
.audit-table td {
  padding: 13px 14px;
  border-bottom: 1px solid #edf0ed;
  vertical-align: top;
}

.audit-table th {
  background: #f7f9f6;
  color: #637368;
  font-weight: 600;
  white-space: nowrap;
}

.audit-table tr:last-child td {
  border-bottom: 0;
}

.audit-table td code {
  display: block;
  max-width: 260px;
  overflow-wrap: anywhere;
  color: #496350;
  font-family: var(--mono);
  font-size: 11px;
}

.audit-outcome {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 999px;
  background: #edf3ed;
  color: #45664e;
  white-space: nowrap;
}

.audit-outcome-denied,
.audit-outcome-failed {
  background: #fbefed;
  color: #a54338;
}

.audit-outcome-cancelled {
  background: #f4f1e8;
  color: #816c34;
}

.audit-retention-note {
  margin-top: 18px;
}

.audit-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 14px;
  color: #718076;
  font-size: 12px;
}

@media (max-width: 760px) {
  .audit-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .audit-filters {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
