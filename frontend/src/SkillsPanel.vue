<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  getSkillDetails,
  getSkills,
  maxSkillArchiveBytes,
  deleteSkill,
  setSkillExecutionMode,
  setSkillEnabled,
  type SkillExecutionMode,
  type SkillCatalogResponse,
  type SkillDetailResponse,
  uploadSkillArchive,
} from './api/skills'
import PaginationControls from './PaginationControls.vue'

const catalog = ref<SkillCatalogResponse | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const selectedSkillId = ref('')
const detail = ref<SkillDetailResponse | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const uploading = ref(false)
const uploadMessage = ref('')
const uploadError = ref('')
const mutationMessage = ref('')
const mutationError = ref('')
const mutatingSkillId = ref('')
const query = ref('')
const sourceFilter = ref('all')
const enabledFilter = ref('all')
const page = ref(1)
const pageSize = 12
const expandedDescriptions = ref<Set<string>>(new Set())
let detailRequestId = 0

const visibleSkills = computed(() => (catalog.value?.skills || []).filter((skill) => {
  const search = query.value.trim().toLocaleLowerCase()
  const matchesSearch = !search || `${skill.name} ${skill.description} ${skill.skill_id}`.toLocaleLowerCase().includes(search)
  const matchesSource = sourceFilter.value === 'all' || skill.source === sourceFilter.value
  const matchesEnabled = enabledFilter.value === 'all'
    || (enabledFilter.value === 'enabled' && skill.enabled)
    || (enabledFilter.value === 'disabled' && !skill.enabled)
  return matchesSearch && matchesSource && matchesEnabled
}))
const pageCount = computed(() => Math.max(1, Math.ceil(visibleSkills.value.length / pageSize)))
const pagedSkills = computed(() => visibleSkills.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([query, sourceFilter, enabledFilter], () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })
watch(pagedSkills, (skills) => {
  if (selectedSkillId.value && !skills.some((skill) => skill.skill_id === selectedSkillId.value)) {
    selectedSkillId.value = ''
    detail.value = null
    detailRequestId += 1
  }
})

function toggleDescription(skillId: string): void {
  const expanded = new Set(expandedDescriptions.value)
  if (expanded.has(skillId)) expanded.delete(skillId)
  else expanded.add(skillId)
  expandedDescriptions.value = expanded
}

async function refresh(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    catalog.value = await getSkills()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取 Skill 目录失败。'
  } finally {
    loading.value = false
  }
}

async function showDetails(skillId: string): Promise<void> {
  if (selectedSkillId.value === skillId) {
    selectedSkillId.value = ''
    detail.value = null
    detailRequestId += 1
    return
  }

  selectedSkillId.value = skillId
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  const requestId = ++detailRequestId
  try {
    const result = await getSkillDetails(skillId)
    if (requestId === detailRequestId) detail.value = result
  } catch (error) {
    if (requestId === detailRequestId) {
      detailError.value = error instanceof Error ? error.message : '读取 Skill 详情失败。'
    }
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}

async function uploadArchive(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  uploadMessage.value = ''
  uploadError.value = ''
  if (!file.name.toLowerCase().endsWith('.zip')) {
    uploadError.value = '请选择 ZIP 格式的 Skill 包。'
    return
  }
  if (file.size > maxSkillArchiveBytes) {
    uploadError.value = 'ZIP 文件不能超过 4 MiB。'
    return
  }

  uploading.value = true
  try {
    const imported = await uploadSkillArchive(file)
    uploadMessage.value = `已导入 ${imported.name}（${imported.skill_id}）。`
    await refresh()
  } catch (error) {
    uploadError.value = error instanceof Error ? error.message : 'Skill ZIP 导入失败。'
  } finally {
    uploading.value = false
  }
}

async function toggleSkill(skill: SkillCatalogResponse['skills'][number]): Promise<void> {
  mutatingSkillId.value = skill.skill_id
  mutationMessage.value = ''
  mutationError.value = ''
  try {
    const updated = await setSkillEnabled(skill.skill_id, !skill.enabled)
    mutationMessage.value = `${updated.name} 已${updated.enabled ? '启用' : '停用'}。`
    await refresh()
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : '更新 Skill 状态失败。'
  } finally {
    mutatingSkillId.value = ''
  }
}

function executionModeLabel(mode: SkillExecutionMode): string {
  const labels: Record<SkillExecutionMode, string> = {
    yaml: 'YAML Runtime',
    markdown: 'Markdown (SKILL.md)',
    both: '两种入口',
  }
  return labels[mode]
}

function availableExecutionModes(
  skill: SkillCatalogResponse['skills'][number],
): SkillExecutionMode[] {
  return skill.available_execution_modes || [skill.execution_format === 'yaml' ? 'yaml' : 'markdown']
}

function effectiveExecutionMode(skill: SkillCatalogResponse['skills'][number]): SkillExecutionMode {
  return skill.execution_mode || availableExecutionModes(skill)[0]
}

function detectedFormats(skill: SkillCatalogResponse['skills'][number]): string[] {
  return (skill.detected_formats || [skill.execution_format]).map(
    (format) => format === 'yaml' ? 'YAML' : 'SKILL.md',
  )
}

async function changeExecutionMode(
  skill: SkillCatalogResponse['skills'][number],
  event: Event,
): Promise<void> {
  const select = event.target as HTMLSelectElement
  const executionMode = select.value as SkillExecutionMode
  if (executionMode === effectiveExecutionMode(skill)) return
  if (!window.confirm(
    `将「${skill.name}」切换为${executionModeLabel(executionMode)}？每次请求只会执行一个入口。`,
  )) return

  mutatingSkillId.value = skill.skill_id
  mutationMessage.value = ''
  mutationError.value = ''
  try {
    const updated = await setSkillExecutionMode(skill.skill_id, executionMode)
    if (catalog.value) {
      catalog.value.skills = catalog.value.skills.map((item) => (
        item.skill_id === updated.skill_id ? updated : item
      ))
    }
    mutationMessage.value = `${skill.name} 的执行方式已更新为${executionModeLabel(executionMode)}。`
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : '更新 Skill 执行方式失败。'
  } finally {
    mutatingSkillId.value = ''
  }
}

async function removeSkill(skill: SkillCatalogResponse['skills'][number]): Promise<void> {
  const confirmed = window.confirm(`确定删除 Skill「${skill.name}」吗？此操作不可撤销。`)
  if (!confirmed) return

  mutatingSkillId.value = skill.skill_id
  mutationMessage.value = ''
  mutationError.value = ''
  try {
    await deleteSkill(skill.skill_id)
    mutationMessage.value = `${skill.name} 已删除。`
    if (selectedSkillId.value === skill.skill_id) {
      selectedSkillId.value = ''
      detail.value = null
      detailRequestId += 1
    }
    await refresh()
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : '删除 Skill 失败。'
  } finally {
    mutatingSkillId.value = ''
  }
}

onMounted(() => void refresh())
</script>

<template>
  <section
    class="skills-page"
    aria-label="Skill 库"
  >
    <header class="skills-page-header">
      <div>
        <span class="skills-kicker">HARNESS SKILL LIBRARY</span>
        <h1>Skill 库</h1>
        <p>浏览当前已发现的 YAML Skill 与 SKILL.md 包。</p>
      </div>
      <div class="skills-page-actions">
        <button
          type="button"
          :disabled="loading"
          @click="refresh"
        >
          {{ loading ? '正在刷新…' : '刷新目录' }}
        </button>
        <label class="skill-upload-button">
          {{ uploading ? '正在导入…' : '导入 ZIP' }}
          <input
            type="file"
            accept=".zip,application/zip"
            :disabled="uploading"
            aria-label="上传 Skill ZIP 包"
            @change="uploadArchive"
          >
        </label>
      </div>
    </header>

    <p
      v-if="uploadMessage"
      class="skills-success"
      role="status"
    >
      {{ uploadMessage }}
    </p>
    <p
      v-if="uploadError"
      class="skills-error"
      role="alert"
    >
      {{ uploadError }}
    </p>
    <p
      v-if="mutationMessage"
      class="skills-success"
      role="status"
    >
      {{ mutationMessage }}
    </p>
    <p
      v-if="mutationError"
      class="skills-error"
      role="alert"
    >
      {{ mutationError }}
    </p>

    <p
      v-if="errorMessage"
      class="skills-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <div
      v-if="loading && !catalog"
      class="skills-empty"
      role="status"
    >
      正在读取 Skill 目录…
    </div>
    <template v-else-if="catalog">
      <p
        v-if="catalog.invalid_package_count"
        class="skills-warning"
        role="status"
      >
        有 {{ catalog.invalid_package_count }} 个 Skill 包未通过校验，目录仅展示有效包。
      </p>
      <div
        v-if="catalog.skills.length"
        class="skills-browse-controls"
      >
        <label>搜索 Skill<input
          v-model="query"
          type="search"
          placeholder="名称、描述或 ID"
        ></label>
        <label>来源<select v-model="sourceFilter"><option value="all">全部来源</option><option value="bundled">随包提供</option><option value="user">本地 Skill</option></select></label>
        <label>状态<select v-model="enabledFilter"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
        <span>{{ visibleSkills.length }} 个 Skill</span>
      </div>
      <div
        v-if="catalog.skills.length && pagedSkills.length"
        class="skills-grid"
      >
        <article
          v-for="skill in pagedSkills"
          :key="skill.skill_id"
          class="skill-card"
        >
          <div class="skill-card-heading">
            <span class="skill-format">{{ detectedFormats(skill).join(' + ') }}</span>
            <span :class="['skill-enabled', { 'is-disabled': !skill.enabled }]">
              {{ skill.enabled ? '已启用' : '已停用' }}
            </span>
          </div>
          <h2>{{ skill.name }}</h2>
          <p
            :class="['skill-description', { 'skill-description-expanded': expandedDescriptions.has(skill.skill_id) }]"
            :title="skill.description"
          >
            {{ skill.description }}
          </p>
          <button
            v-if="skill.description.length > 140"
            class="skill-description-toggle"
            type="button"
            :aria-expanded="expandedDescriptions.has(skill.skill_id)"
            @click="toggleDescription(skill.skill_id)"
          >
            {{ expandedDescriptions.has(skill.skill_id) ? '收起描述' : '展开完整描述' }}
          </button>
          <div class="skill-card-meta">
            <code>{{ skill.skill_id }}</code>
            <span>{{ skill.source === 'bundled' ? '随包提供' : '本地 Skill' }}</span>
          </div>
          <label class="skill-execution-setting">
            <span>执行方式</span>
            <select
              v-if="availableExecutionModes(skill).length > 1"
              :value="effectiveExecutionMode(skill)"
              :disabled="Boolean(mutatingSkillId)"
              :aria-label="`${skill.name} 执行方式`"
              @change="changeExecutionMode(skill, $event)"
            >
              <option
                v-for="mode in availableExecutionModes(skill)"
                :key="mode"
                :value="mode"
              >
                {{ executionModeLabel(mode) }}
              </option>
            </select>
            <strong v-else>{{ executionModeLabel(effectiveExecutionMode(skill)) }}</strong>
          </label>
          <button
            class="skill-details-button"
            type="button"
            :aria-expanded="selectedSkillId === skill.skill_id"
            @click="showDetails(skill.skill_id)"
          >
            {{ selectedSkillId === skill.skill_id ? '收起文件' : '查看文件' }}
          </button>
          <div
            v-if="skill.source === 'user'"
            class="skill-admin-actions"
          >
            <button
              type="button"
              :disabled="Boolean(mutatingSkillId)"
              @click="toggleSkill(skill)"
            >
              {{ mutatingSkillId === skill.skill_id ? '正在更新…' : skill.enabled ? '停用' : '启用' }}
            </button>
            <button
              class="skill-delete-button"
              type="button"
              :disabled="Boolean(mutatingSkillId)"
              @click="removeSkill(skill)"
            >
              删除
            </button>
          </div>
        </article>
      </div>
      <div
        v-if="catalog.skills.length && !pagedSkills.length"
        class="skills-empty"
      >
        没有符合条件的 Skill。
      </div>
      <div
        v-else-if="!catalog.skills.length"
        class="skills-empty"
      >
        当前没有可用的 Skill 包。
      </div>
      <PaginationControls
        v-model:page="page"
        :page-size="pageSize"
        :total="visibleSkills.length"
      />
      <section
        v-if="selectedSkillId"
        class="skill-detail-panel"
        aria-label="Skill 文件详情"
      >
        <header class="skill-detail-header">
          <h2>{{ selectedSkillId }} · 文件详情</h2>
          <button
            type="button"
            @click="showDetails(selectedSkillId)"
          >
            关闭
          </button>
        </header>
        <p
          v-if="detailLoading"
          role="status"
          class="skills-empty"
        >
          正在读取文件…
        </p>
        <p
          v-else-if="detailError"
          role="alert"
          class="skills-error"
        >
          {{ detailError }}
        </p>
        <article
          v-for="file in detail?.files ?? []"
          :key="file.relative_path"
          class="skill-file"
        >
          <h3>{{ file.relative_path }}</h3>
          <pre><code>{{ file.content }}</code></pre>
        </article>
      </section>
    </template>
    <p class="skills-note">
      ZIP 导入支持一个有效 YAML 或 SKILL.md 包及 UTF-8 文本资源（含 .py、.sh、.js，最大 4 MiB）。脚本只作为 Skill 资源导入，不会在导入或加载时运行；执行脚本须调用对应的隔离沙箱工具并经过用户确认。
      导入后可在 Skill 详情中检查文件。
      执行方式按包内有效格式限制；选择“两种入口”时，路由每次只执行 YAML 或 Markdown 中的一种。修改前会提示确认。
    </p>
  </section>
</template>

<style scoped>
.skills-page {
  width: min(100% - 40px, 1280px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.skills-page-header,
.skill-card-heading,
.skill-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.skills-page-header {
  align-items: flex-end;
  margin-bottom: 24px;
}

.skills-page-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.skills-page-actions button { min-height: 44px; }

.skills-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.skills-page-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.skills-page-header p,
.skills-note,
.skill-description {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.skills-page-header button {
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  font-size: 12px;
  cursor: pointer;
}

.skill-upload-button {
  position: relative;
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #385744;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

.skill-upload-button input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}

.skills-page-header button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 14px;
}

.skill-card {
  padding: 20px;
  border: 1px solid #e2e9e2;
  border-radius: 13px;
  background: #fff;
}

.skill-format,
.skill-enabled {
  color: #42694f;
  font-size: 11px;
  font-weight: 650;
}

.skill-enabled.is-disabled {
  color: #9b6b39;
}

.skill-card h2 {
  margin: 18px 0 7px;
  font-size: 18px;
}

.skill-description {
  min-height: 44px;
  margin-bottom: 18px;
}

.skill-card-meta {
  padding-top: 12px;
  border-top: 1px solid #edf1ed;
  color: #718076;
  font-size: 11px;
}

.skill-card-meta code {
  overflow-wrap: anywhere;
  color: #52675a;
}

.skill-execution-setting {
  display: grid;
  gap: 6px;
  margin-top: 13px;
  color: #718076;
  font-size: 11px;
}

.skill-execution-setting select {
  min-height: 34px;
  padding: 0 9px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  background: #fff;
  color: #385744;
  font-size: 11px;
}

.skill-execution-setting select:disabled {
  opacity: 0.65;
}

.skill-execution-setting strong {
  color: #385744;
  font-weight: 600;
}

.skill-details-button,
.skill-detail-header button,
.skill-admin-actions button {
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  background: #fff;
  color: #385744;
  font-size: 11px;
  cursor: pointer;
}

.skill-details-button {
  width: 100%;
  margin-top: 14px;
}

.skill-admin-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.skill-admin-actions button {
  flex: 1;
}

.skill-admin-actions button:disabled {
  cursor: wait;
  opacity: 0.6;
}

.skill-admin-actions .skill-delete-button {
  border-color: #ead4d0;
  color: #963e32;
}

.skill-detail-panel {
  margin-top: 20px;
  padding: 20px;
  border: 1px solid #dce4dc;
  border-radius: 13px;
  background: #f9fbf9;
}

.skill-detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.skill-detail-header h2 {
  overflow-wrap: anywhere;
  font-size: 16px;
}

.skill-file + .skill-file {
  margin-top: 16px;
}

.skill-file h3 {
  margin-bottom: 8px;
  font-family: var(--mono);
  font-size: 12px;
}

.skill-file pre {
  max-height: 480px;
  overflow: auto;
  padding: 14px;
  border: 1px solid #e2e9e2;
  border-radius: 8px;
  background: #fff;
  color: #35473b;
  font: 11px/1.7 var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.skills-empty,
.skills-warning,
.skills-error {
  padding: 18px;
  border: 1px solid #e2e9e2;
  border-radius: 12px;
  background: #fff;
  color: #718076;
  font-size: 13px;
}

.skills-success {
  margin-bottom: 14px;
  padding: 14px 18px;
  border: 1px solid #cfe3d2;
  border-radius: 12px;
  background: #f6fbf6;
  color: #42694f;
  font-size: 13px;
}

.skills-warning {
  margin-bottom: 14px;
  border-color: #eee1c8;
  color: #8a6737;
}

.skills-error {
  border-color: #f0d4d0;
  color: #963e32;
}

.skills-note {
  margin-top: 20px;
}

.skills-browse-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 18px 0; }
.skills-browse-controls label { display: grid; gap: 5px; color: #52665a; font-size: 12px; }
.skills-browse-controls input, .skills-browse-controls select { min-width: 160px; min-height: 42px; padding: 0 10px; border: 1px solid #dce4dc; border-radius: 8px; background: white; }
.skills-browse-controls > span { padding: 0 0 12px; color: #718076; font-size: 12px; }
.skill-description { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow-wrap: anywhere; }
.skill-description-expanded { display: block; overflow: visible; }
.skill-description-toggle { min-height: 44px; padding: 0 8px; border: 0; background: transparent; color: #37664a; cursor: pointer; }

@media (max-width: 560px) {
  .skills-page-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .skills-browse-controls { align-items: stretch; }
  .skills-browse-controls label, .skills-browse-controls input, .skills-browse-controls select { width: 100%; }
  .skills-page-actions { width: 100%; }
}
</style>
