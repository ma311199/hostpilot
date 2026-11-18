<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  deleteMCPServer,
  getMCPServers,
  getToolConnections,
  saveMCPServer,
  type MCPServerConfiguration,
  type MCPServerConfigurationInput,
  type MCPServerState,
  type ToolConnectionState,
  type ToolConnectionsResponse,
} from './api/mcp'

const response = ref<ToolConnectionsResponse | null>(null)
const servers = ref<MCPServerConfiguration[]>([])
const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const importJson = ref('')
const importing = ref(false)
const configurationMode = ref<'parameters' | 'json'>('parameters')
const editorOpen = ref(false)
const editingName = ref<string | null>(null)
const form = reactive({
  name: '',
  transport: 'streamable_http' as 'streamable_http' | 'stdio',
  url: '',
  command: 'npx',
  args: '',
  cwd: '',
  timeout_seconds: 15,
  enabled: true,
  credential_name: '',
  api_key: '',
})

async function refresh(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [connections, configurations] = await Promise.all([
      getToolConnections(),
      getMCPServers(),
    ])
    response.value = connections
    servers.value = configurations.servers
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取 MCP 连接状态失败。'
  } finally {
    loading.value = false
  }
}

function clearForm(): void {
  editorOpen.value = false
  configurationMode.value = 'parameters'
  editingName.value = null
  Object.assign(form, {
    name: '',
    transport: 'streamable_http',
    url: '',
    command: 'npx',
    args: '',
    cwd: '',
    timeout_seconds: 15,
    enabled: true,
    credential_name: '',
    api_key: '',
  })
}

function editServer(server: MCPServerConfiguration): void {
  if (editorOpen.value) {
    closeEditor()
    if (editorOpen.value) return
  }
  editorOpen.value = true
  configurationMode.value = 'parameters'
  editingName.value = server.name
  Object.assign(form, {
    name: server.name,
    transport: server.transport,
    url: server.url || '',
    command: server.command || 'npx',
    args: server.args.join('\n'),
    cwd: server.cwd || '',
    timeout_seconds: server.timeout_seconds,
    enabled: server.enabled,
    credential_name: server.transport === 'stdio'
      ? server.api_key_env || ''
      : server.api_key_header || '',
    api_key: '',
  })
  errorMessage.value = ''
  successMessage.value = ''
}

function addServer(): void {
  if (editorOpen.value) {
    closeEditor()
    if (editorOpen.value) return
  }
  clearForm()
  editorOpen.value = true
}

function closeEditor(): void {
  const hasDraft = form.name || form.url || form.args || form.api_key || importJson.value
  if (hasDraft && !window.confirm('放弃当前未保存的配置吗？')) return
  clearForm()
}

async function saveServer(): Promise<void> {
  if (saving.value) return
  errorMessage.value = ''
  successMessage.value = ''
  const processArguments = form.transport === 'stdio'
    ? form.args.split('\n').map((argument) => argument.trim()).filter(Boolean)
    : []
  if (containsCredentialArgument(processArguments)) {
    errorMessage.value = '凭证不能放在 STDIO args 中，请使用单独的 API Key 字段。'
    return
  }
  const configuration: MCPServerConfigurationInput = {
    name: form.name.trim(),
    transport: form.transport,
    url: form.transport === 'streamable_http' ? form.url.trim() : null,
    command: form.transport === 'stdio' ? form.command.trim() : null,
    args: processArguments,
    cwd: form.transport === 'stdio' ? form.cwd.trim() || null : null,
    timeout_seconds: form.timeout_seconds,
    enabled: form.enabled,
    api_key_header: form.transport === 'streamable_http' ? form.credential_name.trim() || null : null,
    api_key_env: form.transport === 'stdio' ? form.credential_name.trim() || null : null,
  }
  if (form.api_key) configuration.api_key = form.api_key
  saving.value = true
  try {
    const result = await saveMCPServer(configuration)
    servers.value = result.servers
    form.api_key = ''
    clearForm()
    successMessage.value = 'MCP Server 配置已保存。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存 MCP Server 配置失败。'
  } finally {
    saving.value = false
  }
}

async function importServerJson(): Promise<void> {
  if (importing.value) return
  errorMessage.value = ''
  successMessage.value = ''
  let parsed: unknown
  try {
    parsed = JSON.parse(importJson.value)
  } catch {
    errorMessage.value = 'JSON 格式无效，请检查后重试。'
    return
  }
  if (!isRecord(parsed) || !isRecord(parsed.mcpServers)) {
    errorMessage.value = '配置必须包含 mcpServers 对象。'
    return
  }

  const entries = Object.entries(parsed.mcpServers)
  if (entries.length === 0) {
    errorMessage.value = 'mcpServers 中没有可导入的 Server。'
    return
  }
  const configurations: MCPServerConfigurationInput[] = []
  const allowedCommands = new Set([
    'npx', 'npx.cmd', 'node', 'node.exe', 'uvx', 'uvx.exe', 'python', 'python.exe',
    'python3', 'python3.exe', 'bun', 'bun.exe', 'deno', 'deno.exe',
  ])

  for (const [name, value] of entries) {
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name) || !isRecord(value)) {
      errorMessage.value = `Server「${name}」的名称或配置结构无效。`
      return
    }
    if (hasNonEmptyRecord(value.env) || hasNonEmptyRecord(value.headers)) {
      errorMessage.value = `Server「${name}」包含凭证字段；请在连接表单中单独输入密钥。`
      return
    }
    const timeoutSeconds = value.timeout_seconds ?? value.timeoutSeconds ?? 15
    if (typeof timeoutSeconds !== 'number' || timeoutSeconds < 1 || timeoutSeconds > 120) {
      errorMessage.value = `Server「${name}」的超时时间须在 1 到 120 秒之间。`
      return
    }
    if (typeof value.command === 'string') {
      const executable = value.command.split(/[\\/]/).pop()?.toLowerCase() || ''
      if (!allowedCommands.has(executable) && !/^python3\.\d+(\.exe)?$/.test(executable)) {
        errorMessage.value = `Server「${name}」的启动命令不在支持列表中。`
        return
      }
      if (value.args !== undefined && (
        !Array.isArray(value.args)
        || value.args.length > 128
        || value.args.some((argument) => typeof argument !== 'string' || argument.length > 4096)
      )) {
        errorMessage.value = `Server「${name}」的 args 必须是最多 128 项的字符串数组。`
        return
      }
      const processArguments = (value.args as string[] | undefined) || []
      if (containsCredentialArgument(processArguments)) {
        errorMessage.value = `Server「${name}」的凭证不能放在 args 中，请在连接表单中单独输入密钥。`
        return
      }
      if (value.cwd !== undefined && typeof value.cwd !== 'string') {
        errorMessage.value = `Server「${name}」的 cwd 必须是字符串。`
        return
      }
      configurations.push({
        name,
        transport: 'stdio',
        url: null,
        command: value.command,
        args: processArguments,
        cwd: (value.cwd as string | undefined) || null,
        timeout_seconds: timeoutSeconds,
        enabled: true,
        api_key_header: null,
        api_key_env: null,
      })
      continue
    }
    if (typeof value.url === 'string') {
      let endpoint: URL
      try {
        endpoint = new URL(value.url)
      } catch {
        errorMessage.value = `Server「${name}」的 URL 格式无效。`
        return
      }
      const sensitiveQueryNames = new Set([
        'api_key', 'apikey', 'authorization', 'access_token', 'token', 'secret', 'password',
      ])
      if (
        !['http:', 'https:'].includes(endpoint.protocol)
        || endpoint.username
        || endpoint.password
        || endpoint.hash
        || [...endpoint.searchParams.keys()].some((key) => sensitiveQueryNames.has(key.toLowerCase()))
      ) {
        errorMessage.value = `Server「${name}」的 URL 不可包含凭证或片段。`
        return
      }
      configurations.push({
        name,
        transport: 'streamable_http',
        url: value.url,
        command: null,
        args: [],
        cwd: null,
        timeout_seconds: timeoutSeconds,
        enabled: true,
        api_key_header: null,
        api_key_env: null,
      })
      continue
    }
    errorMessage.value = `Server「${name}」须提供 command 或 url。`
    return
  }

  importing.value = true
  try {
    let result: { servers: MCPServerConfiguration[] } | null = null
    for (const configuration of configurations) {
      result = await saveMCPServer(configuration)
    }
    servers.value = result?.servers || []
    importJson.value = ''
    clearForm()
    successMessage.value = `已导入 ${configurations.length} 个 MCP Server 配置。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入 MCP Server 配置失败。'
  } finally {
    importing.value = false
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasNonEmptyRecord(value: unknown): boolean {
  return isRecord(value) && Object.keys(value).length > 0
}

function containsCredentialArgument(arguments_: string[]): boolean {
  const credentialName = /api[-_.]?key|access[-_.]?token|refresh[-_.]?token|auth(?:orization)?|password|passwd|secret|credential|token|private[-_.]?key|client[-_.]?secret/i
  const credentialAssignment = /(?:^|[?&;=\s])(?:api[-_.]?key|access[-_.]?token|refresh[-_.]?token|authorization|auth[-_.]?token|password|passwd|secret|credential|token|private[-_.]?key|client[-_.]?secret)\s*[:=]\s*\S+/i
  return arguments_.some((argument) => {
    const option = argument.match(/^--?([A-Za-z0-9_.-]+)(?:=(.*))?$/)
    const assignments = [...argument.matchAll(/(?:^|[?&;\s])([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*\S+/g)]
    return Boolean(
      (option && credentialName.test(option[1]))
      || credentialAssignment.test(argument),
    ) || assignments.some((assignment) => credentialName.test(assignment[1]))
  })
}

async function removeServer(server: MCPServerConfiguration): Promise<void> {
  if (!server.managed) return
  if (!window.confirm(`删除 MCP Server「${server.name}」配置及当前进程内的 API Key？`)) return
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await deleteMCPServer(server.name)
    servers.value = servers.value.filter((item) => item.name !== server.name)
    if (editingName.value === server.name) clearForm()
    successMessage.value = 'MCP Server 配置已删除。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除 MCP Server 配置失败。'
  }
}

function stateLabel(state: ToolConnectionState): string {
  const labels: Record<ToolConnectionState, string> = {
    connecting: '正在连接',
    connected: '已连接',
    disconnected: '未连接',
    failed: '连接失败',
  }
  return labels[state]
}

function transportLabel(transport: string): string {
  if (transport === 'in_process') return '进程内 MCP'
  return transport === 'stdio' ? '本地 STDIO' : 'Streamable HTTP'
}

function serverStateLabel(state: MCPServerState): string {
  const labels: Record<MCPServerState, string> = {
    connecting: '正在连接',
    connected: '已连接',
    disconnected: '未连接',
    failed: '连接失败',
    waiting_for_credentials: '等待 API Key',
    disabled: '已停用',
  }
  return labels[state]
}

function discoveredToolCount(serverName: string): number {
  return response.value?.connections.find(
    (connection) => connection.connection_id === serverName,
  )?.discovered_tool_count || 0
}

onMounted(() => void refresh())
</script>

<template>
  <section
    class="connections-page"
    aria-label="MCP 连接管理"
  >
    <header class="connections-page-header">
      <div>
        <span class="connections-kicker">HARNESS CONNECTIONS</span>
        <h1>MCP 连接</h1>
        <p>管理本机 STDIO 与远程 HTTP MCP Server，并查看内置和外部连接状态。</p>
      </div>
      <button
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '正在刷新…' : '刷新状态' }}
      </button>
    </header>

    <p
      v-if="errorMessage"
      class="connections-error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <p
      v-if="successMessage"
      class="connections-success"
      role="status"
    >
      {{ successMessage }}
    </p>
    <div
      v-if="loading && !response"
      class="connections-empty"
      role="status"
    >
      正在读取 MCP 连接状态…
    </div>
    <div
      v-else-if="response?.connections.length"
      class="connections-grid"
    >
      <article
        v-for="connection in response.connections"
        :key="connection.connection_id"
        class="connection-card"
      >
        <div class="connection-card-heading">
          <span class="connection-transport">{{ transportLabel(connection.transport) }}</span>
          <span :class="['connection-state', `state-${connection.state}`]">
            <i aria-hidden="true" />
            {{ stateLabel(connection.state) }}
          </span>
        </div>
        <h2>{{ connection.connection_id }}</h2>
        <div class="connection-card-meta">
          <span>发现工具</span>
          <strong>{{ connection.discovered_tool_count }}</strong>
        </div>
        <p
          v-if="connection.error_code"
          class="connection-error-code"
        >
          错误类型：{{ connection.error_code }}
        </p>
      </article>
    </div>
    <div
      v-else-if="response && !errorMessage"
      class="connections-empty"
    >
      当前没有已配置的 MCP 连接。
    </div>
    <section
      class="server-management"
      aria-labelledby="server-management-title"
    >
      <header class="server-management-heading">
        <div>
          <span class="connections-kicker">MCP SERVERS</span>
          <h2 id="server-management-title">
            MCP Server 配置
          </h2>
        </div>
        <div class="server-management-heading-actions">
          <button
            type="button"
            @click="addServer"
          >
            添加 Server
          </button>
        </div>
      </header>
      <div
        v-if="servers.length"
        class="server-list"
      >
        <article
          v-for="server in servers"
          :key="server.name"
          class="server-row"
        >
          <div class="server-row-info">
            <strong>{{ server.name }}</strong>
            <span :class="['connection-state', `state-${server.connection_state}`]">
              <i aria-hidden="true" />
              {{ serverStateLabel(server.connection_state) }}
            </span>
            <small v-if="server.transport === 'streamable_http'">{{ server.url }}</small>
            <small v-else>{{ server.command }} {{ server.args.join(' ') }}</small>
            <small>发现工具 {{ discoveredToolCount(server.name) }}</small>
            <small v-if="server.api_key_header || server.api_key_env">
              {{ server.api_key_configured ? 'API Key 已提供（仅当前进程）' : '需要重新输入 API Key' }}
            </small>
          </div>
          <div class="server-row-actions">
            <button
              v-if="server.managed"
              type="button"
              @click="editServer(server)"
            >
              编辑
            </button>
            <button
              v-if="server.managed"
              type="button"
              @click="removeServer(server)"
            >
              删除
            </button>
            <span v-else>由环境变量管理</span>
          </div>
        </article>
      </div>
      <p
        v-else
        class="server-list-empty"
      >
        还没有外部 Server 配置。
      </p>
      <div
        v-if="editorOpen"
        class="server-editor"
      >
        <div
          class="server-config-mode"
          role="group"
          aria-label="MCP 配置模式"
        >
          <button
            type="button"
            :aria-pressed="configurationMode === 'parameters'"
            @click="configurationMode = 'parameters'"
          >
            参数配置
          </button>
          <button
            type="button"
            :aria-pressed="configurationMode === 'json'"
            @click="configurationMode = 'json'"
          >
            JSON 导入
          </button>
          <button
            type="button"
            class="server-editor-close"
            @click="closeEditor"
          >
            关闭
          </button>
        </div>
        <form
          v-if="configurationMode === 'json'"
          class="server-import-form"
          aria-label="导入 MCP Server JSON"
          @submit.prevent="importServerJson"
        >
          <label>
            <span>从 JSON 导入 MCP Server</span>
            <textarea
              v-model="importJson"
              rows="7"
              spellcheck="false"
              placeholder="粘贴包含 mcpServers 的 JSON；支持 command/args 的 STDIO 和 url 的 HTTP 配置"
            />
          </label>
          <p class="server-form-note">
            导入会保存每个 Server 的非敏感连接配置。含 env、headers 或凭证型 args 的 JSON 不会导入，请在下方表单单独配置密钥。
          </p>
          <button
            type="submit"
            :disabled="importing || !importJson.trim()"
          >
            {{ importing ? '正在导入…' : '导入 JSON 配置' }}
          </button>
        </form>
        <form
          v-else
          class="server-form"
          @submit.prevent="saveServer"
        >
          <h3>{{ editingName ? `编辑 ${editingName}` : '添加 MCP Server' }}</h3>
          <label>
            <span>Server ID</span>
            <input
              v-model="form.name"
              required
              pattern="[a-z][a-z0-9_-]{0,31}"
              maxlength="32"
              :disabled="Boolean(editingName)"
              placeholder="例如 research"
            >
          </label>
          <label>
            <span>传输方式</span>
            <select
              v-model="form.transport"
              :disabled="Boolean(editingName)"
            >
              <option value="streamable_http">远程 Streamable HTTP</option>
              <option value="stdio">本机 STDIO 子进程</option>
            </select>
          </label>
          <template v-if="form.transport === 'streamable_http'">
            <label>
              <span>Streamable HTTP URL</span>
              <input
                v-model="form.url"
                required
                type="url"
                maxlength="2048"
                placeholder="https://mcp.example.com/mcp"
              >
            </label>
          </template>
          <template v-else>
            <label>
              <span>启动命令</span>
              <input
                v-model="form.command"
                required
                maxlength="512"
                placeholder="npx"
              >
            </label>
            <label class="server-form-wide">
              <span>命令参数（每行一个参数）</span>
              <textarea
                v-model="form.args"
                rows="4"
                placeholder="-y&#10;@modelcontextprotocol/server-filesystem&#10;/path/to/project"
              />
              <small>不要把密钥写入启动参数；凭证请使用单独的 API Key 字段。</small>
            </label>
            <label>
              <span>工作目录 <em>可选</em></span>
              <input
                v-model="form.cwd"
                maxlength="2048"
                placeholder="/path/to/project"
              >
            </label>
          </template>
          <label>
            <span>连接超时（秒）</span>
            <input
              v-model.number="form.timeout_seconds"
              required
              type="number"
              min="1"
              max="120"
              step="1"
            >
          </label>
          <label class="server-enabled-option">
            <input
              v-model="form.enabled"
              type="checkbox"
            >
            <span>启用连接</span>
          </label>
          <label>
            <span>{{ form.transport === 'stdio' ? 'API Key 环境变量' : 'API Key Header' }} <em>可选</em></span>
            <input
              v-model="form.credential_name"
              maxlength="128"
              :placeholder="form.transport === 'stdio' ? '例如 API_KEY' : '例如 Authorization'"
            >
          </label>
          <label v-if="form.credential_name.trim()">
            <span>API Key <em>{{ editingName ? '留空则保留当前进程密钥' : '连接所需' }}</em></span>
            <input
              v-model="form.api_key"
              type="password"
              autocomplete="new-password"
              maxlength="8192"
              placeholder="重启后请重新输入"
            >
          </label>
          <p
            v-if="form.transport === 'stdio'"
            class="server-form-note server-form-wide"
          >
            命令会在本机以子进程启动，不经过 Shell；子进程不会继承 HostPilot 的模型、RAG 等环境密钥，但与 HostPilot 共享当前操作系统用户权限，并非沙箱。请只配置你信任的 MCP 包。
          </p>
          <footer class="server-form-actions">
            <button
              type="button"
              @click="closeEditor"
            >
              取消
            </button>
            <button
              type="submit"
              :disabled="saving"
            >
              {{ saving ? '正在保存…' : '保存并连接' }}
            </button>
          </footer>
        </form>
      </div>
    </section>
    <p class="connections-privacy-note">
      Server 连接配置保存在本机；API Key 只保存在后端进程内存，不返回给页面或写入浏览器存储。环境变量管理的 Server 仅展示状态。
    </p>
  </section>
</template>

<style scoped>
.connections-page {
  width: min(100% - 40px, 1280px);
  margin: 0 auto;
  padding: clamp(34px, 6vw, 70px) 0 64px;
  color: #263b30;
}

.connections-page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.connections-kicker {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}

.connections-page-header h1 {
  margin-top: 10px;
  font-size: clamp(30px, 5vw, 46px);
  letter-spacing: -1.7px;
}

.connections-page-header p,
.connections-privacy-note {
  color: #718076;
  font-size: 13px;
  line-height: 1.7;
}

.connections-page-header button {
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid #dce4dc;
  border-radius: 8px;
  background: #fff;
  color: #385744;
  font-size: 12px;
  cursor: pointer;
}

.connections-page-header button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.connections-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 14px;
}

.connection-card {
  padding: 20px;
  border: 1px solid #e2e9e2;
  border-radius: 13px;
  background: #fff;
}

.connection-card-heading,
.connection-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.connection-transport {
  color: #7c8c7f;
  font-family: var(--mono);
  font-size: 10px;
}

.connection-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #8b7656;
  font-size: 11px;
}

.connection-state i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.state-connected {
  color: #43825a;
}

.state-failed {
  color: #b85045;
}

.state-connecting {
  color: #a87931;
}

.state-waiting_for_credentials,
.state-disconnected,
.state-disabled {
  color: #8b7656;
}

.server-management {
  margin-top: 36px;
  padding: 22px;
  border: 1px solid #e2e9e2;
  border-radius: 13px;
  background: #fff;
}

.server-management-heading,
.server-row,
.server-row-actions,
.server-form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.server-management-heading h2 {
  margin-top: 8px;
  font-size: 21px;
}

.server-management-heading-actions,
.server-config-mode {
  display: flex;
  align-items: center;
  gap: 8px;
}

.server-config-mode {
  padding: 3px;
  border: 1px solid #e2e9e2;
  border-radius: 9px;
  background: #f7f9f7;
}

.server-config-mode button[aria-pressed='true'] {
  border-color: #c5d7c8;
  background: #eaf2eb;
  color: #284e34;
}

.server-management button {
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  background: #fff;
  color: #385744;
  font-size: 12px;
  cursor: pointer;
}

.server-management button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.server-list {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}

.server-row {
  padding: 14px;
  border: 1px solid #edf1ed;
  border-radius: 9px;
}

.server-row-info {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.server-row-info strong {
  font-family: var(--mono);
  font-size: 13px;
}

.server-row-info small,
.server-row-actions span,
.server-list-empty {
  color: #718076;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.server-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 13px;
  margin-top: 22px;
  padding-top: 20px;
  border-top: 1px solid #edf1ed;
}

.server-import-form {
  display: grid;
  gap: 10px;
  margin-top: 20px;
  padding: 16px;
  border: 1px solid #edf1ed;
  border-radius: 9px;
  background: #fbfcfb;
}

.server-import-form label {
  display: grid;
  gap: 6px;
  color: #54675a;
  font-size: 11px;
}

.server-import-form textarea {
  width: 100%;
  padding: 9px 10px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  color: #263b30;
  font-family: var(--mono);
  font-size: 11px;
  resize: vertical;
}

.server-import-form button {
  justify-self: end;
}

.server-form h3,
.server-form-actions {
  grid-column: 1 / -1;
}

.server-form h3 {
  margin: 0;
  font-size: 15px;
}

.server-form label {
  display: grid;
  gap: 6px;
  color: #54675a;
  font-size: 11px;
}

.server-form input:not([type='checkbox']),
.server-form select,
.server-form textarea {
  width: 100%;
  min-height: 37px;
  padding: 0 10px;
  border: 1px solid #dce4dc;
  border-radius: 7px;
  color: #263b30;
  font-size: 12px;
}

.server-form textarea {
  padding: 9px 10px;
  resize: vertical;
}

.server-form-wide {
  grid-column: 1 / -1;
}

.server-form-note {
  margin: 0;
  color: #718076;
  font-size: 11px;
  line-height: 1.6;
}

.server-form input:disabled {
  background: #f3f6f3;
  color: #718076;
}

.server-enabled-option {
  align-items: center;
  grid-template-columns: auto 1fr !important;
}

.server-form-actions {
  justify-content: flex-end;
}

.connections-success {
  color: #43825a;
  font-size: 12px;
}

.connection-card h2 {
  margin: 18px 0;
  color: #263b30;
  font-family: var(--mono);
  font-size: 15px;
  overflow-wrap: anywhere;
}

.connection-card-meta {
  padding-top: 14px;
  border-top: 1px solid #edf1ed;
  color: #718076;
  font-size: 12px;
}

.connection-card-meta strong {
  color: #385744;
  font-size: 15px;
}

.connection-error-code,
.connections-error {
  color: #a5443a;
  font-size: 12px;
}

.connections-empty {
  padding: 28px;
  border: 1px dashed #dce4dc;
  border-radius: 12px;
  color: #718076;
  text-align: center;
  font-size: 13px;
}

.connections-privacy-note {
  margin-top: 20px;
}

@media (max-width: 600px) {
  .connections-page-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .server-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .server-management-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .server-management-heading-actions {
    width: 100%;
    justify-content: space-between;
  }

  .server-form {
    grid-template-columns: 1fr;
  }
}

.server-editor { margin-top: 18px; padding: clamp(14px, 2.5vw, 24px); border: 1px solid #e2e9e2; border-radius: 14px; background: #fbfcfa; }
.server-editor-close { margin-left: auto; }
</style>
