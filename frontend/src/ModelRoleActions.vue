<script setup lang="ts">
import type { ModelRole } from './api/model'

defineProps<{
  role: ModelRole
  savingRole: ModelRole | null
  testingRole: ModelRole | null
  result: { success: boolean; message: string } | null
}>()

defineEmits<{ save: []; test: [] }>()
</script>

<template>
  <div class="model-role-actions">
    <p
      v-if="result"
      :class="['model-role-result', result.success ? 'is-success' : 'is-error']"
      role="status"
    >
      {{ result.message }}
    </p>
    <div class="model-role-buttons">
      <button
        class="model-test-button"
        type="button"
        :disabled="Boolean(savingRole || testingRole)"
        @click="$emit('test')"
      >
        {{ testingRole === role ? '正在测试…' : '测试连接' }}
      </button>
      <button
        class="model-save-button"
        type="button"
        :disabled="Boolean(savingRole || testingRole)"
        @click="$emit('save')"
      >
        {{ savingRole === role ? '正在保存…' : '保存配置' }}
      </button>
    </div>
  </div>
</template>
