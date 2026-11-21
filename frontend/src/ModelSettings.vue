<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'

import {
  getModelConfiguration,
  saveModelConfiguration,
  testModelConnection,
  type ModelConnectionTestResult,
  type ModelRole,
  type ModelConfigurationStatus,
} from './api/model'
import {
  getRoutingModelConfiguration,
  saveRoutingModelConfiguration,
  type RoutingModelConfigurationStatus,
} from './api/routingModel'
import ModelRoleActions from './ModelRoleActions.vue'

const emit = defineEmits<{ close: [] }>()

type OptionalRole = Exclude<ModelRole, 'llm'>
type ResultState = Pick<ModelConnectionTestResult, 'success' | 'message'>

const status = ref<ModelConfigurationStatus | null>(null)
const modelRoles = ref<RoutingModelConfigurationStatus | null>(null)
const modelName = ref('')
const lightweightModel = ref('')
const embeddingModel = ref('')
const rerankerModel = ref('')
const apiKey = ref('')
const baseUrl = ref('')
const lightweightApiKey = ref('')
const lightweightBaseUrl = ref('')
const embeddingApiKey = ref('')
const embeddingBaseUrl = ref('')
const rerankerApiKey = ref('')
const rerankerBaseUrl = ref('')
const streaming = ref(true)
const loading = ref(true)
const savingRole = ref<ModelRole | null>(null)
const testingRole = ref<ModelRole | null>(null)
const results = reactive<Record<ModelRole, ResultState | null>>({
  llm: null,
  judge: null,
  embedding: null,
  reranker: null,
})
let previouslyFocusedElement: HTMLElement | null = null

onMounted(async () => {
  previouslyFocusedElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  document.querySelector<HTMLElement>('.model-settings-dialog button, .model-settings-dialog input')?.focus()
  try {
    const [modelStatus, rolesStatus] = await Promise.all([
      getModelConfiguration(),
      getRoutingModelConfiguration(),
    ])
    status.value = modelStatus
    modelRoles.value = rolesStatus
    modelName.value = modelStatus.model_name || ''
    baseUrl.value = modelStatus.base_url || ''
    lightweightModel.value = rolesStatus.judge_model || ''
    embeddingModel.value = rolesStatus.embedding_model || ''
    rerankerModel.value = rolesStatus.reranker_model || ''
    lightweightBaseUrl.value = rolesStatus.judge_base_url || ''
    embeddingBaseUrl.value = rolesStatus.embedding_base_url || ''
    rerankerBaseUrl.value = rolesStatus.reranker_base_url || ''
    streaming.value = modelStatus.streaming
  } catch (error) {
    results.llm = { success: false, message: messageFor(error) }
  } finally {
    loading.value = false
  }
})

onUnmounted(() => previouslyFocusedElement?.focus())

function handleDialogKeydown(event: KeyboardEvent): void {
  const dialog = event.currentTarget as HTMLElement
  const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => element.getAttribute('aria-hidden') !== 'true')
  const first = focusable[0]
  const last = focusable[focusable.length - 1]

  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  } else if (event.key === 'Tab' && first && last) {
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

function modelFor(role: ModelRole): string {
  if (role === 'llm') return modelName.value
  if (role === 'judge') return lightweightModel.value
  if (role === 'embedding') return embeddingModel.value
  return rerankerModel.value
}

function keyFor(role: ModelRole): string {
  if (role === 'llm') return apiKey.value
  if (role === 'judge') return lightweightApiKey.value
  if (role === 'embedding') return embeddingApiKey.value
  return rerankerApiKey.value
}

function baseUrlFor(role: ModelRole): string {
  if (role === 'llm') return baseUrl.value
  if (role === 'judge') return lightweightBaseUrl.value
  if (role === 'embedding') return embeddingBaseUrl.value
  return rerankerBaseUrl.value
}

function hasConfiguredKey(role: ModelRole): boolean {
  if (role === 'llm') return Boolean(status.value?.configured)
  if (role === 'judge') return Boolean(modelRoles.value?.judge_api_key_configured)
  if (role === 'embedding') return Boolean(modelRoles.value?.embedding_api_key_configured)
  return Boolean(modelRoles.value?.reranker_api_key_configured)
}

function roleLabel(role: ModelRole): string {
  return {
    llm: 'LLM Model',
    judge: '轻量 LLM Model',
    embedding: 'Embedding Model',
    reranker: 'Reranker Model',
  }[role]
}

function roleSummary(role: ModelRole): string {
  const name = modelFor(role).trim()
  if (!name) return role === 'llm' ? '必需配置' : '可选 · 未配置'
  const hasKey = hasConfiguredKey(role) || Boolean(keyFor(role).trim())
  return hasKey ? `已配置 · ${name}` : `需要 API Key · ${name}`
}

async function saveRole(role: ModelRole): Promise<void> {
  if (savingRole.value || testingRole.value) return
  const model = modelFor(role).trim()
  if (role === 'llm' && !model) {
    results.llm = { success: false, message: 'LLM Model 是必需项，请先填写模型名称。' }
    return
  }
  savingRole.value = role
  results[role] = null
  try {
    if (role === 'llm') {
      status.value = await saveModelConfiguration({
        model_name: model,
        api_key: apiKey.value.trim() || undefined,
        base_url: baseUrl.value.trim() || null,
        streaming: streaming.value,
      })
      apiKey.value = ''
    } else {
      modelRoles.value = await saveRoutingModelConfiguration(optionalRoleInput(role as OptionalRole))
      clearRoleKey(role)
    }
    results[role] = { success: true, message: `${roleLabel(role)}已保存并启用。` }
  } catch (error) {
    results[role] = { success: false, message: messageFor(error) }
  } finally {
    savingRole.value = null
  }
}

function optionalRoleInput(role: OptionalRole) {
  if (role === 'judge') {
    return {
      judge_model: lightweightModel.value.trim() || null,
      judge_api_key: lightweightApiKey.value.trim() || undefined,
      judge_base_url: lightweightBaseUrl.value.trim() || null,
    }
  }
  if (role === 'embedding') {
    return {
      embedding_model: embeddingModel.value.trim() || null,
      embedding_api_key: embeddingApiKey.value.trim() || undefined,
      embedding_base_url: embeddingBaseUrl.value.trim() || null,
    }
  }
  return {
    reranker_model: rerankerModel.value.trim() || null,
    reranker_api_key: rerankerApiKey.value.trim() || undefined,
    reranker_base_url: rerankerBaseUrl.value.trim() || null,
  }
}

function clearRoleKey(role: ModelRole): void {
  if (role === 'judge') lightweightApiKey.value = ''
  if (role === 'embedding') embeddingApiKey.value = ''
  if (role === 'reranker') rerankerApiKey.value = ''
}

async function testRole(role: ModelRole): Promise<void> {
  if (testingRole.value || savingRole.value) return
  const model = modelFor(role).trim()
  if (!model) {
    results[role] = {
      success: false,
      message: role === 'llm' ? '请先填写 LLM Model。' : '请先填写模型名称。',
    }
    return
  }
  testingRole.value = role
  results[role] = null
  try {
    const result = await testModelConnection({
      role,
      model_name: model,
      api_key: keyFor(role).trim() || undefined,
      base_url: baseUrlFor(role).trim() || null,
      ...(role === 'llm' ? { streaming: streaming.value } : {}),
    })
    results[role] = { success: result.success, message: result.message }
  } catch (error) {
    results[role] = { success: false, message: messageFor(error) }
  } finally {
    testingRole.value = null
  }
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请检查输入后重试。'
}
</script>

<template>
  <div
    class="model-settings-backdrop"
    @pointerdown.self="emit('close')"
  >
    <section
      class="model-settings-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-settings-title"
      tabindex="-1"
      @keydown="handleDialogKeydown"
    >
      <header class="model-settings-header">
        <div>
          <span class="chat-kicker">MODEL PROVIDER</span>
          <h2 id="model-settings-title">
            模型连接
          </h2>
        </div>
        <button
          class="model-settings-close"
          type="button"
          aria-label="关闭"
          @click="emit('close')"
        >
          ×
        </button>
      </header>

      <p class="model-settings-intro">
        每个模型角色独立配置、测试和保存。测试连接会发送一条最小请求，不会保存未提交的配置。
      </p>

      <div
        v-if="loading"
        class="model-settings-loading"
      >
        正在读取模型状态…
      </div>
      <div
        v-else
        class="model-role-list"
      >
        <details
          class="model-role-card"
          open
        >
          <summary>
            <span class="model-role-number">01</span>
            <span class="model-role-heading">
              <strong>LLM Model</strong>
              <small>{{ roleSummary('llm') }}</small>
            </span>
            <span class="model-role-required">必需</span>
            <span
              class="model-role-chevron"
              aria-hidden="true"
            >⌄</span>
          </summary>
          <div class="model-role-body">
            <label>
              <span>模型名称</span>
              <input
                v-model="modelName"
                required
                maxlength="200"
                placeholder="例如 gpt-4.1-mini"
              >
            </label>
            <label>
              <span>API Key <em>{{ status?.configured ? '留空则沿用当前密钥' : '必需' }}</em></span>
              <input
                v-model="apiKey"
                :required="status?.api_key_required || !status?.configured"
                type="password"
                autocomplete="new-password"
                maxlength="8192"
                placeholder="LLM Model 服务商的 API Key"
              >
            </label>
            <label>
              <span>Base URL <em>可选</em></span>
              <input
                v-model="baseUrl"
                type="url"
                maxlength="2048"
                placeholder="https://api.openai.com/v1"
              >
            </label>
            <label class="model-streaming-option">
              <input
                v-model="streaming"
                type="checkbox"
              >
              <span><strong>使用流式接口</strong><small>适用于只支持 SSE 流式响应的模型服务。</small></span>
            </label>
            <ModelRoleActions
              :role="'llm'"
              :saving-role="savingRole"
              :testing-role="testingRole"
              :result="results.llm"
              @save="saveRole('llm')"
              @test="testRole('llm')"
            />
          </div>
        </details>

        <details class="model-role-card">
          <summary>
            <span class="model-role-number">02</span>
            <span class="model-role-heading">
              <strong>轻量 LLM Model</strong>
              <small>{{ roleSummary('judge') }}</small>
            </span>
            <span class="model-role-optional">可选</span>
            <span
              class="model-role-chevron"
              aria-hidden="true"
            >⌄</span>
          </summary>
          <div class="model-role-body">
            <label>
              <span>模型名称</span>
              <input
                v-model="lightweightModel"
                maxlength="200"
                placeholder="留空时使用主 LLM"
              >
            </label>
            <label>
              <span>API Key <em>{{ modelRoles?.judge_api_key_configured ? '留空则沿用当前密钥' : '配置模型时必填' }}</em></span>
              <input
                v-model="lightweightApiKey"
                type="password"
                autocomplete="new-password"
                maxlength="8192"
                placeholder="轻量 LLM API Key；重启后需重新输入"
              >
            </label>
            <label>
              <span>Base URL <em>可选</em></span>
              <input
                v-model="lightweightBaseUrl"
                type="url"
                maxlength="2048"
                placeholder="https://api.openai.com/v1"
              >
            </label>
            <ModelRoleActions
              :role="'judge'"
              :saving-role="savingRole"
              :testing-role="testingRole"
              :result="results.judge"
              @save="saveRole('judge')"
              @test="testRole('judge')"
            />
          </div>
        </details>

        <details class="model-role-card">
          <summary>
            <span class="model-role-number">03</span>
            <span class="model-role-heading">
              <strong>Embedding Model</strong>
              <small>{{ roleSummary('embedding') }}</small>
            </span>
            <span class="model-role-optional">可选</span>
            <span
              class="model-role-chevron"
              aria-hidden="true"
            >⌄</span>
          </summary>
          <div class="model-role-body">
            <label>
              <span>模型名称</span>
              <input
                v-model="embeddingModel"
                maxlength="200"
                placeholder="留空时使用 BM25 词项召回"
              >
            </label>
            <label>
              <span>API Key <em>{{ modelRoles?.embedding_api_key_configured ? '已本机保存；留空可保留' : '配置模型时必填' }}</em></span>
              <input
                v-model="embeddingApiKey"
                type="password"
                autocomplete="new-password"
                maxlength="8192"
                placeholder="Embedding API Key；本机持久保存"
              >
            </label>
            <label>
              <span>Base URL <em>可选</em></span>
              <input
                v-model="embeddingBaseUrl"
                type="url"
                maxlength="2048"
                placeholder="https://api.openai.com/v1"
              >
            </label>
            <ModelRoleActions
              :role="'embedding'"
              :saving-role="savingRole"
              :testing-role="testingRole"
              :result="results.embedding"
              @save="saveRole('embedding')"
              @test="testRole('embedding')"
            />
          </div>
        </details>

        <details class="model-role-card">
          <summary>
            <span class="model-role-number">04</span>
            <span class="model-role-heading">
              <strong>Reranker Model</strong>
              <small>{{ roleSummary('reranker') }}</small>
            </span>
            <span class="model-role-optional">可选</span>
            <span
              class="model-role-chevron"
              aria-hidden="true"
            >⌄</span>
          </summary>
          <div class="model-role-body">
            <label>
              <span>模型名称</span>
              <input
                v-model="rerankerModel"
                maxlength="200"
                placeholder="未配置时跳过重排"
              >
            </label>
            <label>
              <span>API Key <em>{{ modelRoles?.reranker_api_key_configured ? '已本机保存；留空可保留' : '配置模型时必填' }}</em></span>
              <input
                v-model="rerankerApiKey"
                type="password"
                autocomplete="new-password"
                maxlength="8192"
                placeholder="Reranker API Key；本机持久保存"
              >
            </label>
            <label>
              <span>Base URL <em>可选</em></span>
              <input
                v-model="rerankerBaseUrl"
                type="url"
                maxlength="2048"
                placeholder="https://api.openai.com/v1"
              >
            </label>
            <ModelRoleActions
              :role="'reranker'"
              :saving-role="savingRole"
              :testing-role="testingRole"
              :result="results.reranker"
              @save="saveRole('reranker')"
              @test="testRole('reranker')"
            />
          </div>
        </details>

        <p class="model-settings-note">
          Embedding 故障时回退到 BM25；Reranker 故障时跳过重排。轻量 LLM 缺失或不可用时使用主 LLM。
          Embedding 与 Reranker Key 保存在本机 SQLite；主 LLM、轻量 LLM 和 MCP Key 重启后需重新输入。
        </p>
        <footer class="model-settings-actions">
          <button
            class="model-cancel-button"
            type="button"
            @click="emit('close')"
          >
            关闭
          </button>
        </footer>
      </div>
    </section>
  </div>
</template>
