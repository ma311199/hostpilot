<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  getModelConfiguration,
  saveModelConfiguration,
  type ModelConfigurationStatus,
} from './api/model'

const emit = defineEmits<{ close: [] }>()

const status = ref<ModelConfigurationStatus | null>(null)
const modelName = ref('')
const apiKey = ref('')
const baseUrl = ref('')
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

onMounted(async () => {
  try {
    status.value = await getModelConfiguration()
    if (status.value.configured) {
      modelName.value = status.value.model_name || ''
      baseUrl.value = status.value.base_url || ''
    }
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    loading.value = false
  }
})

async function save(): Promise<void> {
  if (saving.value) return
  errorMessage.value = ''
  successMessage.value = ''
  saving.value = true
  try {
    status.value = await saveModelConfiguration({
      model_name: modelName.value.trim(),
      api_key: apiKey.value,
      base_url: baseUrl.value.trim() || null,
    })
    apiKey.value = ''
    successMessage.value = '模型运行时已配置，可以返回聊天页面发送消息。'
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    saving.value = false
  }
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : '配置失败，请检查输入后重试。'
}
</script>

<template>
  <div
    class="model-settings-backdrop"
    @click.self="emit('close')"
  >
    <section
      class="model-settings-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-settings-title"
    >
      <header class="model-settings-header">
        <div>
          <span class="chat-kicker">MODEL PROVIDER</span>
          <h2 id="model-settings-title">
            配置模型
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
        配置 OpenAI 兼容接口。API Key 只发送给本机后端并保存在当前进程内存中，不会返回到页面或写入浏览器存储。
      </p>

      <div
        v-if="loading"
        class="model-settings-loading"
      >
        正在读取模型状态…
      </div>
      <template v-else>
        <div
          v-if="status?.configured"
          class="model-current-status"
          role="status"
        >
          <span class="model-status-dot" />
          当前已配置：<strong>{{ status.model_name }}</strong>
          <span v-if="status.base_url"> · {{ status.base_url }}</span>
        </div>
        <div
          v-else
          class="model-current-status model-status-empty"
        >
          尚未配置模型，配置后即可开始聊天。
        </div>

        <form
          class="model-settings-form"
          @submit.prevent="save"
        >
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
            <span>API Key</span>
            <input
              v-model="apiKey"
              required
              type="password"
              autocomplete="new-password"
              maxlength="8192"
              placeholder="输入模型服务商提供的 API Key"
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

          <p
            v-if="errorMessage"
            class="model-settings-error"
            role="alert"
          >
            {{ errorMessage }}
          </p>
          <p
            v-if="successMessage"
            class="model-settings-success"
            role="status"
          >
            {{ successMessage }}
          </p>
          <p class="model-settings-note">
            配置仅在后端进程运行期间有效；重启后可重新输入，或通过后端环境变量配置。
          </p>
          <footer class="model-settings-actions">
            <button
              class="model-cancel-button"
              type="button"
              @click="emit('close')"
            >
              关闭
            </button>
            <button
              class="model-save-button"
              type="submit"
              :disabled="saving"
            >
              {{ saving ? '正在保存…' : '保存并启用' }}
            </button>
          </footer>
        </form>
      </template>
    </section>
  </div>
</template>
