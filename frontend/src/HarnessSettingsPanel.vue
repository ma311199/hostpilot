<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  getHarnessConfiguration,
  saveHarnessConfiguration,
  type HarnessConfiguration,
} from './api/harness'

const configuration = reactive<HarnessConfiguration>({
  max_context_messages: 40,
  max_memory_context_chars: 16000,
  max_request_chars: 32000,
  max_response_chars: 100000,
  request_token_budget: 20000,
  request_tool_call_budget: 5,
  request_retrieval_call_budget: 2,
  request_timeout_seconds: 60,
  plan_max_tasks: 10,
  plan_tool_agent_max_turns: 5,
  plan_tool_candidate_limit: 5,
  plan_result_history_limit: 32,
  plan_result_history_max_chars: 65536,
  routing_minimum_business_confidence: 0.85,
  routing_minimum_business_margin: 0.3,
  routing_minimum_plan_candidate_confidence: 0.5,
  routing_max_plan_candidates: 5,
  routing_max_business_candidates: 32,
})
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

onMounted(async () => {
  try {
    Object.assign(configuration, await getHarnessConfiguration())
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    loading.value = false
  }
})

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    Object.assign(configuration, await saveHarnessConfiguration({ ...configuration }))
    successMessage.value = 'Harness 配置已保存，并立即应用于后续请求。'
  } catch (error) {
    errorMessage.value = messageFor(error)
  } finally {
    saving.value = false
  }
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'Harness 配置失败，请检查输入后重试。'
}
</script>

<template>
  <section
    class="harness-settings-page"
    aria-label="Harness 配置"
  >
    <header class="harness-settings-header">
      <div>
        <span class="harness-settings-kicker">RUNTIME CONTROLS</span>
        <h1>Harness 配置</h1>
        <p>设置上下文、预算、Plan 子 Agent 和路由边界，保存后立即用于后续请求，并在本机跨重启保留。</p>
      </div>
    </header>

    <p
      v-if="errorMessage"
      class="harness-settings-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <p
      v-if="successMessage"
      class="harness-settings-success"
      role="status"
    >
      {{ successMessage }}
    </p>
    <div
      v-if="loading"
      class="harness-settings-loading"
      role="status"
    >
      正在读取 Harness 配置…
    </div>
    <form
      v-else
      class="harness-settings-form"
      @submit.prevent="save"
    >
      <details
        class="harness-settings-group"
        open
      >
        <summary class="harness-settings-group-heading">
          <h2>上下文与消息限制</h2>
          <p>控制每次请求可以读取的会话内容和输入输出大小。</p>
        </summary>
        <label>
          <span>最多读取的历史消息数</span>
          <input
            v-model.number="configuration.max_context_messages"
            type="number"
            min="1"
            max="500"
            required
          >
        </label>
        <label>
          <span>记忆上下文字符上限</span>
          <input
            v-model.number="configuration.max_memory_context_chars"
            type="number"
            min="1"
            max="100000"
            required
          >
        </label>
        <label>
          <span>单条请求字符上限</span>
          <input
            v-model.number="configuration.max_request_chars"
            type="number"
            min="1"
            max="100000"
            required
          >
        </label>
        <label>
          <span>单条响应字符上限</span>
          <input
            v-model.number="configuration.max_response_chars"
            type="number"
            min="1"
            max="200000"
            required
          >
        </label>
      </details>

      <details class="harness-settings-group">
        <summary class="harness-settings-group-heading">
          <h2>Plan 子 Agent</h2>
          <p>每个工具子任务由受限 ReAct Agent 循环执行。历史成功结果仅供 Planner 参考，不代表本轮已完成；实际工具调用仍受总预算、权限和审批控制。</p>
        </summary>
        <label>
          <span>Plan 最多拆分子任务数</span>
          <input
            v-model.number="configuration.plan_max_tasks"
            type="number"
            min="1"
            max="100"
            aria-label="Plan 最多拆分子任务数"
            required
          >
        </label>
        <label>
          <span>每个子任务最多 ReAct 轮数</span>
          <input
            v-model.number="configuration.plan_tool_agent_max_turns"
            type="number"
            min="1"
            max="32"
            aria-label="每个子任务最多 ReAct 轮数"
            required
          >
        </label>
        <label>
          <span>每个任务最多检索工具数</span>
          <input
            v-model.number="configuration.plan_tool_candidate_limit"
            type="number"
            min="1"
            max="20"
            aria-label="每个任务最多检索工具数"
            required
          >
        </label>
        <label>
          <span>Plan 历史成功结果条数</span>
          <input
            v-model.number="configuration.plan_result_history_limit"
            type="number"
            min="1"
            max="128"
            aria-label="Plan 历史成功结果条数"
            required
          >
          <small>每个新 Plan 最多读取当前会话之前的最近记录；不会删除 SQLite 中的旧记录。</small>
        </label>
        <label>
          <span>Plan 历史结果总字符数</span>
          <input
            v-model.number="configuration.plan_result_history_max_chars"
            type="number"
            min="1"
            max="262144"
            aria-label="Plan 历史结果总字符数"
            required
          >
          <small>按任务说明、输入、依赖、输出和执行摘要累计；历史内容仅作为 Planner 参考。</small>
        </label>
      </details>

      <details class="harness-settings-group">
        <summary class="harness-settings-group-heading">
          <h2>路由阈值与候选数量</h2>
          <p>置信度使用 0 到 1；业务 Skill 和进入 Plan 的候选门槛最低为 0.5，防止低置信结果直接执行。</p>
        </summary>
        <label>
          <span>业务 Skill 高置信门槛</span>
          <input
            v-model.number="configuration.routing_minimum_business_confidence"
            type="number"
            min="0.5"
            max="1"
            step="0.01"
            aria-label="业务 Skill 高置信门槛"
            required
          >
        </label>
        <label>
          <span>业务 Skill 最小置信差</span>
          <input
            v-model.number="configuration.routing_minimum_business_margin"
            type="number"
            min="0"
            max="1"
            step="0.01"
            aria-label="业务 Skill 最小置信差"
            required
          >
        </label>
        <label>
          <span>进入 Plan 的最低候选置信度</span>
          <input
            v-model.number="configuration.routing_minimum_plan_candidate_confidence"
            type="number"
            min="0.5"
            max="1"
            step="0.01"
            aria-label="进入 Plan 的最低候选置信度"
            required
          >
        </label>
        <label>
          <span>传给 Plan 的最多 Skill 候选</span>
          <input
            v-model.number="configuration.routing_max_plan_candidates"
            type="number"
            min="1"
            max="20"
            aria-label="传给 Plan 的最多 Skill 候选"
            required
          >
        </label>
        <label>
          <span>路由最多检索业务 Skill</span>
          <input
            v-model.number="configuration.routing_max_business_candidates"
            type="number"
            min="1"
            max="100"
            aria-label="路由最多检索业务 Skill"
            required
          >
        </label>
      </details>

      <details class="harness-settings-group">
        <summary class="harness-settings-group-heading">
          <h2>单次请求预算</h2>
          <p>预算耗尽或超时后，请求会停止并返回对应状态。</p>
        </summary>
        <label>
          <span>Token 预算</span>
          <input
            v-model.number="configuration.request_token_budget"
            type="number"
            min="1"
            aria-label="Token 预算"
            required
          >
        </label>
        <label>
          <span>工具调用次数</span>
          <input
            v-model.number="configuration.request_tool_call_budget"
            type="number"
            min="0"
            max="100"
            aria-label="工具调用次数"
            required
          >
        </label>
        <label>
          <span>知识检索次数</span>
          <input
            v-model.number="configuration.request_retrieval_call_budget"
            type="number"
            min="0"
            max="100"
            required
          >
        </label>
        <label>
          <span>请求超时（秒）</span>
          <input
            v-model.number="configuration.request_timeout_seconds"
            type="number"
            min="0.1"
            max="600"
            step="0.1"
            required
          >
        </label>
      </details>

      <div class="harness-settings-footer">
        <p>模型连接与 API Key 在“模型配置”中管理；MCP Server 在“MCP 连接”中管理。</p>
        <button
          type="submit"
          :disabled="saving"
        >
          {{ saving ? '正在保存…' : '保存配置' }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.harness-settings-page {
  width: min(100% - 40px, 1280px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.harness-settings-header {
  margin-bottom: 24px;
}

.harness-settings-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.harness-settings-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.harness-settings-header p,
.harness-settings-group-heading p,
.harness-settings-footer p {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.harness-settings-form {
  display: grid;
  gap: 16px;
}

.harness-settings-group {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  padding: 20px;
  border: 1px solid #e2e9e2;
  border-radius: 13px;
  background: #fff;
}

.harness-settings-group-heading {
  display: block;
  cursor: pointer;
}

.harness-settings-group-heading::marker { color: #52745d; }
.harness-settings-group-heading:focus-visible { outline: 3px solid rgba(71, 167, 121, 0.28); outline-offset: 4px; border-radius: 4px; }

.harness-settings-group-heading {
  grid-column: 1 / -1;
}

.harness-settings-group-heading h2 {
  font-size: 17px;
}

.harness-settings-group-heading p {
  margin-top: 5px;
  font-size: 12px;
}

.harness-settings-group label {
  display: grid;
  gap: 7px;
  color: #54675a;
  font-size: 12px;
}

.harness-settings-group input {
  width: 100%;
  min-height: 39px;
  padding: 0 11px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  color: #263b30;
  font-size: 13px;
}

.harness-settings-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.harness-settings-footer button {
  min-height: 40px;
  padding: 0 16px;
  border: 0;
  border-radius: 8px;
  background: #315d3e;
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
}

.harness-settings-footer button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.harness-settings-error,
.harness-settings-success,
.harness-settings-loading {
  padding: 13px 15px;
  border-radius: 9px;
  font-size: 12px;
}

.harness-settings-error {
  background: #fff3f0;
  color: #a5443a;
}

.harness-settings-success {
  background: #edf7ef;
  color: #3b7750;
}

.harness-settings-loading {
  border: 1px dashed #dce4dc;
  color: #718076;
  text-align: center;
}

@media (max-width: 600px) {
  .harness-settings-group {
    grid-template-columns: 1fr;
    padding: 16px;
  }

  .harness-settings-footer {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
