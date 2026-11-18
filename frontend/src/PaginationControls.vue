<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ page: number; pageSize: number; total: number }>()
const emit = defineEmits<{ 'update:page': [page: number] }>()

const pageCount = computed(() => Math.ceil(props.total / props.pageSize))
const start = computed(() => (props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1))
const end = computed(() => Math.min(props.page * props.pageSize, props.total))
</script>

<template>
  <nav
    v-if="pageCount > 1"
    class="pagination-controls"
    aria-label="分页"
  >
    <span class="pagination-range">{{ start }}–{{ end }} / {{ total }}</span>
    <div class="pagination-actions">
      <button
        type="button"
        aria-label="上一页"
        :disabled="page <= 1"
        @click="emit('update:page', page - 1)"
      >
        上一页
      </button>
      <span aria-current="page">{{ page }}</span>
      <button
        type="button"
        aria-label="下一页"
        :disabled="page >= pageCount"
        @click="emit('update:page', page + 1)"
      >
        下一页
      </button>
    </div>
  </nav>
</template>

<style scoped>
.pagination-controls,
.pagination-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.pagination-controls {
  flex-wrap: wrap;
  color: var(--text-muted, #667085);
  font-size: 0.875rem;
}

.pagination-actions button {
  min-width: 44px;
  min-height: 44px;
}

.pagination-actions [aria-current="page"] {
  min-width: 2rem;
  text-align: center;
  font-weight: 600;
}
</style>
