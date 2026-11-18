import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  deleteSkill,
  getSkillDetails,
  getSkills,
  setSkillEnabled,
  setSkillExecutionMode,
  uploadSkillArchive,
} from './skills'

describe('Skill catalog API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads safe package metadata from the local API', async () => {
    const payload = {
      skills: [
        {
          skill_id: 'business/meeting-notes',
          name: 'Meeting notes',
          description: 'Turn notes into minutes.',
          execution_format: 'yaml',
          enabled: true,
          source: 'bundled',
        },
      ],
      invalid_package_count: 0,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getSkills()).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/v1/skills')
  })

  it('reports an unavailable API without exposing request details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network detail')))

    await expect(getSkills()).rejects.toThrow('无法连接后端')
  })

  it('requests Skill files by encoded catalog ID', async () => {
    const payload = {
      skill_id: 'business/code-review-check',
      files: [{ relative_path: 'skill.yaml', content: 'meta: {}' }],
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(getSkillDetails('business/code-review-check')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/skills/detail?skill_id=business%2Fcode-review-check',
    )
  })

  it('uploads a ZIP as a bounded raw request body', async () => {
    const payload = {
      skill_id: 'legacy/meeting-notes',
      name: 'meeting-notes',
      description: 'Turn notes into minutes.',
      execution_format: 'skill_md',
      enabled: true,
      source: 'user',
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['zip bytes'], 'meeting-notes.zip', { type: 'application/zip' })

    await expect(uploadSkillArchive(file)).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/skills/import',
      expect.objectContaining({ method: 'POST', body: file }),
    )
  })

  it('updates enabled state and deletes by catalog ID', async () => {
    const enabledPayload = {
      skill_id: 'legacy/meeting-notes',
      name: 'meeting-notes',
      description: 'Turn notes into minutes.',
      execution_format: 'skill_md',
      enabled: false,
      source: 'user',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(enabledPayload), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(setSkillEnabled('legacy/meeting-notes', false)).resolves.toEqual(enabledPayload)
    await expect(deleteSkill('legacy/meeting-notes')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://127.0.0.1:8000/api/v1/skills/state',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ skill_id: 'legacy/meeting-notes', enabled: false }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:8000/api/v1/skills?skill_id=legacy%2Fmeeting-notes',
      { method: 'DELETE' },
    )
  })

  it('updates execution mode through the local configuration API', async () => {
    const payload = {
      skill_id: 'business/combined',
      name: 'Combined',
      description: 'Works in two modes.',
      execution_format: 'yaml',
      detected_formats: ['yaml', 'skill_md'],
      execution_mode: 'both',
      available_execution_modes: ['yaml', 'deepagents', 'both'],
      enabled: true,
      source: 'user',
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(setSkillExecutionMode('business/combined', 'both')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/skills/execution',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ skill_id: 'business/combined', execution_mode: 'both' }),
      }),
    )
  })
})
