<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { getToolCatalog, type ToolCatalog } from './api/tools'
import PaginationControls from './PaginationControls.vue'

const catalog = ref<ToolCatalog | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const query = ref('')
const selectedType = ref('all')
const selectedState = ref('all')
const page = ref(1)
const expandedDescriptions = ref<Set<string>>(new Set())
const pageSize = 12
const toolTypeLabels: Record<string, string> = {
  retrieval: '知识检索',
  action: '本地操作',
  code: '代码执行',
}
const transportLabels: Record<string, string> = {
  in_process: '内置',
  stdio: 'STDIO MCP',
  streamable_http: 'Streamable HTTP MCP',
}
const trustLabels: Record<string, string> = {
  host: 'HostPilot 内置',
  third_party: '第三方',
  untrusted: '不可信代码',
}
const runtimeLabel = computed(() =>
  catalog.value?.runtime_configured ? '模型运行时已就绪' : '请先配置模型运行时',
)
const visibleTools = computed(() => (catalog.value?.tools || []).filter((tool) => {
  const search = query.value.trim().toLocaleLowerCase()
  const matchesSearch = !search || `${tool.name} ${tool.description}`.toLocaleLowerCase().includes(search)
  const matchesType = selectedType.value === 'all' || tool.tool_type === selectedType.value
  const state = availabilityLabel(tool.configured)
  const matchesState = selectedState.value === 'all'
    || (selectedState.value === 'ready' && state === '可调用')
    || (selectedState.value === 'waiting' && state !== '可调用')
  return matchesSearch && matchesType && matchesState
}))
const pageCount = computed(() => Math.max(1, Math.ceil(visibleTools.value.length / pageSize)))
const pagedTools = computed(() => visibleTools.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([query, selectedType, selectedState], () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })

function toggleDescription(name: string): void {
  const expanded = new Set(expandedDescriptions.value)
  if (expanded.has(name)) expanded.delete(name)
  else expanded.add(name)
  expandedDescriptions.value = expanded
}

async function refresh(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    catalog.value = await getToolCatalog()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取工具目录失败。'
  } finally {
    loading.value = false
  }
}

function availabilityLabel(configured: boolean): string {
  if (!configured) return '未配置'
  return catalog.value?.runtime_configured ? '可调用' : '等待模型'
}

onMounted(() => void refresh())
</script>

<template>
  <section
    class="tools-page"
    aria-label="工具目录"
  >
    <header class="tools-page-header">
      <div>
        <span class="tools-kicker">HOSTPILOT CAPABILITIES</span>
        <h1>工具目录</h1>
        <p>查看 Agent 可以调用的本地工具、配置状态和审批要求。</p>
      </div>
      <button
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '正在刷新…' : '刷新目录' }}
      </button>
    </header>

    <div
      v-if="catalog"
      class="tools-runtime-status"
      role="status"
    >
      <span :class="['tools-status-dot', { 'tools-status-ready': catalog.runtime_configured }]" />
      {{ runtimeLabel }}
    </div>
    <p
      v-if="errorMessage"
      class="tools-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <div
      v-if="catalog?.tools.length"
      class="tools-browse-controls"
    >
      <label>搜索工具<input
        v-model="query"
        type="search"
        placeholder="名称或描述"
      ></label>
      <label>分类<select v-model="selectedType"><option value="all">全部分类</option><option value="retrieval">知识检索</option><option value="action">本地操作</option><option value="code">代码执行</option></select></label>
      <label>状态<select v-model="selectedState"><option value="all">全部状态</option><option value="ready">可调用</option><option value="waiting">等待配置</option></select></label>
      <span class="tools-result-count">{{ visibleTools.length }} 项工具</span>
    </div>
    <div
      v-if="loading && !catalog"
      class="tools-empty"
    >
      正在读取工具目录…
    </div>
    <div
      v-else-if="catalog?.tools.length && pagedTools.length"
      class="tools-grid"
    >
      <article
        v-for="tool in pagedTools"
        :key="tool.name"
        class="tool-card"
      >
        <div class="tool-card-heading">
          <span class="tool-type-label">
            {{ toolTypeLabels[tool.tool_type] || tool.tool_type }}
          </span>
          <span
            :class="[
              'tool-availability',
              { 'tool-availability-ready': tool.configured && catalog.runtime_configured },
            ]"
          >
            {{ availabilityLabel(tool.configured) }}
          </span>
        </div>
        <h2>{{ tool.name }}</h2>
        <p
          :class="['tool-description', { 'tool-description-expanded': expandedDescriptions.has(tool.name) }]"
          :title="tool.description"
        >
          {{ tool.description }}
        </p>
        <button
          v-if="tool.description.length > 140"
          class="tool-description-toggle"
          type="button"
          :aria-expanded="expandedDescriptions.has(tool.name)"
          @click="toggleDescription(tool.name)"
        >
          {{ expandedDescriptions.has(tool.name) ? '收起描述' : '展开完整描述' }}
        </button>
        <dl class="tool-permission-details">
          <div>
            <dt>能力范围</dt>
            <dd>{{ tool.access_summary }}</dd>
          </div>
          <div>
            <dt>运行位置</dt>
            <dd>{{ tool.execution_location }}</dd>
          </div>
          <div>
            <dt>来源与信任</dt>
            <dd>
              {{ transportLabels[tool.transport] || tool.transport }} ·
              {{ trustLabels[tool.trust_level] || tool.trust_level }}
            </dd>
          </div>
          <div>
            <dt>沙箱</dt>
            <dd>{{ tool.sandbox_status }}</dd>
          </div>
        </dl>
        <div class="tool-card-footer">
          <span
            v-if="tool.confirmation_required"
            class="tool-confirmation-tag"
          >
            高影响操作 · 每次执行前需审批
          </span>
          <span
            v-else
            class="tool-readonly-tag"
          >
            只读或低影响
          </span>
        </div>
        <p class="tool-status-message">
          {{ tool.status_message }}
        </p>
      </article>
    </div>
    <div
      v-if="catalog?.tools.length && !pagedTools.length"
      class="tools-empty"
    >
      没有符合条件的工具。
    </div>
    <PaginationControls
      v-model:page="page"
      :page-size="pageSize"
      :total="visibleTools.length"
    />
    <div
      v-if="catalog && !catalog.tools.length && !errorMessage"
      class="tools-empty"
    >
      当前没有注册的工具。
    </div>
    <p class="tools-privacy-note">
      工具密钥和原始参数不会显示在此页面；高影响操作仍须经过策略检查和用户审批。
    </p>
  </section>
</template>

<style scoped>
.tools-page {
  width: min(100% - 40px, 1280px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.tools-page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.tools-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.tools-page-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.tools-page-header p,
.tool-description,
.tool-status-message,
.tools-privacy-note {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.tools-page-header button {
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  font-size: 12px;
  cursor: pointer;
}

.tools-page-header button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.tools-runtime-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  color: #52665a;
  font-size: 12px;
}

.tools-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c38b43;
}

.tools-status-ready {
  background: #4f9a69;
}

.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 14px;
}

.tool-card {
  padding: 20px;
  border: 1px solid #e2e9e2;
  border-radius: 13px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(38, 59, 48, 0.04);
}

.tool-card-heading,
.tool-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.tool-permission-details {
  display: grid;
  gap: 10px;
  margin: 17px 0;
}

.tool-permission-details > div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.tool-permission-details dt {
  color: #52665a;
  font-size: 11px;
  font-weight: 700;
}

.tool-permission-details dd {
  margin: 0;
  color: #718076;
  font-size: 11px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.tool-type-label,
.tool-availability,
.tool-confirmation-tag,
.tool-readonly-tag {
  border-radius: 20px;
  font-size: 10px;
  font-weight: 650;
}

.tool-type-label {
  padding: 5px 8px;
  background: #f1f5f1;
  color: #55705d;
}

.tool-availability {
  color: #ad7427;
}

.tool-availability-ready {
  color: #397950;
}

.tool-card h2 {
  margin-top: 18px;
  font-family: var(--mono);
  font-size: 15px;
}

.tool-description {
  min-height: 44px;
  margin-top: 8px;
}

.tool-card-footer {
  justify-content: flex-start;
  margin-top: 17px;
}

.tool-confirmation-tag,
.tool-readonly-tag {
  padding: 6px 8px;
}

.tool-confirmation-tag {
  background: #fff5e5;
  color: #96621d;
}

.tool-readonly-tag {
  background: #edf5ef;
  color: #477255;
}

.tool-status-message {
  margin-top: 12px;
  font-size: 11px;
}

.tools-privacy-note {
  margin-top: 20px;
  font-size: 11px;
}

.tools-error {
  margin: 12px 0;
  color: #ac4c3e;
  font-size: 12px;
}

.tools-empty {
  padding: 30px;
  border: 1px dashed #dce4dc;
  border-radius: 12px;
  color: #718076;
  text-align: center;
  font-size: 13px;
}

.tools-browse-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 12px;
  margin: 18px 0;
}

.tools-browse-controls label { display: grid; gap: 5px; color: #52665a; font-size: 12px; }
.tools-browse-controls input, .tools-browse-controls select { min-width: 160px; min-height: 42px; padding: 0 10px; border: 1px solid #dce4dc; border-radius: 8px; background: white; }
.tools-result-count { padding: 0 0 12px; color: #718076; font-size: 12px; }
.tool-description { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow-wrap: anywhere; }
.tool-description-expanded { display: block; overflow: visible; }
.tool-description-toggle { min-height: 44px; padding: 0 8px; border: 0; background: transparent; color: #37664a; cursor: pointer; }

@media (max-width: 560px) {
  .tools-page-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .tools-browse-controls { align-items: stretch; }
  .tools-browse-controls label, .tools-browse-controls input, .tools-browse-controls select { width: 100%; }
}
</style>
