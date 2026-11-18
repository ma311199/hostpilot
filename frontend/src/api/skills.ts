export interface SkillCatalogItem {
  skill_id: string
  name: string
  description: string
  execution_format: 'yaml' | 'skill_md'
  detected_formats: Array<'yaml' | 'skill_md'>
  execution_mode: SkillExecutionMode
  available_execution_modes: SkillExecutionMode[]
  enabled: boolean
  source: 'user' | 'bundled'
}

export type SkillExecutionMode = 'yaml' | 'deepagents' | 'both'

export interface SkillCatalogResponse {
  skills: SkillCatalogItem[]
  invalid_package_count: number
}

export interface SkillFile {
  relative_path: string
  content: string
}

export interface SkillDetailResponse {
  skill_id: string
  files: SkillFile[]
}

export const maxSkillArchiveBytes = 4 * 1024 * 1024

const defaultApiBaseUrl = 'http://127.0.0.1:8000/api/v1'

export async function getSkills(): Promise<SkillCatalogResponse> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills`)
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (!response.ok) {
    throw new Error(`读取 Skill 目录失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as SkillCatalogResponse
}

export async function getSkillDetails(skillId: string): Promise<SkillDetailResponse> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  const query = new URLSearchParams({ skill_id: skillId })
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills/detail?${query.toString()}`)
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (response.status === 404) {
    throw new Error('Skill 已不存在或未通过校验，请刷新目录。')
  }
  if (!response.ok) {
    throw new Error(`读取 Skill 详情失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as SkillDetailResponse
}

export async function uploadSkillArchive(file: File): Promise<SkillCatalogItem> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    })
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (!response.ok) {
    if (response.status === 413) throw new Error('ZIP 文件不能超过 4 MiB。')
    if (response.status === 422) {
      const body = (await response.json()) as { detail?: string }
      throw new Error(body.detail || 'Skill ZIP 校验失败。')
    }
    throw new Error(`导入 Skill 失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as SkillCatalogItem
}

export async function setSkillEnabled(skillId: string, enabled: boolean): Promise<SkillCatalogItem> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills/state`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill_id: skillId, enabled }),
    })
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (!response.ok) {
    if (response.status === 404) throw new Error('Skill 已不存在，请刷新目录。')
    if (response.status === 422) {
      const body = (await response.json()) as { detail?: string }
      throw new Error(body.detail || '更新 Skill 状态失败。')
    }
    throw new Error(`更新 Skill 状态失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as SkillCatalogItem
}

export async function setSkillExecutionMode(
  skillId: string,
  executionMode: SkillExecutionMode,
): Promise<SkillCatalogItem> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills/execution`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill_id: skillId, execution_mode: executionMode }),
    })
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }
  if (!response.ok) {
    if (response.status === 404) throw new Error('Skill 已不存在，请刷新目录。')
    if (response.status === 422) {
      const body = (await response.json()) as { detail?: string }
      throw new Error(body.detail || '所选执行方式与 Skill 格式不兼容。')
    }
    throw new Error(`更新 Skill 执行方式失败（HTTP ${response.status}）。`)
  }
  return (await response.json()) as SkillCatalogItem
}

export async function deleteSkill(skillId: string): Promise<void> {
  const configuredUrl = import.meta.env.VITE_HOSTPILOT_API_BASE_URL as string | undefined
  const apiBaseUrl = (configuredUrl?.trim() || defaultApiBaseUrl).replace(/\/$/, '')
  const query = new URLSearchParams({ skill_id: skillId })
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/skills?${query.toString()}`, { method: 'DELETE' })
  } catch {
    throw new Error('无法连接后端，请确认 FastAPI 服务已启动。')
  }

  if (response.status === 404) throw new Error('Skill 已不存在，请刷新目录。')
  if (!response.ok) throw new Error(`删除 Skill 失败（HTTP ${response.status}）。`)
}
