import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SkillsPanel from './SkillsPanel.vue'

describe('Skill library page', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows validated skill metadata and package state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            skills: [
              {
                skill_id: 'business/meeting-notes',
                name: 'Meeting notes',
                description: 'Turn notes into minutes.',
                execution_format: 'yaml',
                enabled: true,
                source: 'bundled',
              },
              {
                skill_id: 'legacy/research',
                name: 'Research',
                description: 'Collect information.',
                execution_format: 'skill_md',
                enabled: true,
                source: 'user',
              },
            ],
            invalid_package_count: 1,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const wrapper = mount(SkillsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('Meeting notes')
    expect(wrapper.text()).toContain('business/meeting-notes')
    expect(wrapper.text()).toContain('YAML')
    expect(wrapper.text()).toContain('SKILL.md')
    expect(wrapper.text()).toContain('1 个 Skill 包未通过校验')
    expect(wrapper.text()).toContain('随包提供')
    expect(wrapper.find('.skill-admin-actions').exists()).toBe(true)
    expect(wrapper.findAll('.skill-admin-actions')).toHaveLength(1)
  })

  it('filters and paginates the Skill catalog', async () => {
    const skills = Array.from({ length: 14 }, (_, index) => ({
      skill_id: `user/skill-${index + 1}`, name: `Skill ${index + 1}`,
      description: `Description ${index + 1}`, execution_format: 'yaml',
      detected_formats: ['yaml'], execution_mode: 'yaml', available_execution_modes: ['yaml'],
      enabled: index % 2 === 0, source: index % 2 === 0 ? 'user' : 'bundled',
    }))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ skills, invalid_package_count: 0 }))))
    const wrapper = mount(SkillsPanel)
    await flushPromises()
    expect(wrapper.findAll('.skill-card')).toHaveLength(12)
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(wrapper.findAll('.skill-card')).toHaveLength(2)
    await wrapper.get('input[type="search"]').setValue('skill-1')
    expect(wrapper.findAll('.skill-card')).toHaveLength(6)
    await wrapper.get('select').setValue('user')
    expect(wrapper.findAll('.skill-card').every((card) => card.text().includes('本地 Skill'))).toBe(true)
  })

  it('does not show backend exception details when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network detail')))

    const wrapper = mount(SkillsPanel)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('无法连接后端')
    expect(wrapper.text()).not.toContain('network detail')
  })

  it('loads validated text files when a Skill detail is opened', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            skills: [
              {
                skill_id: 'business/code-review-check',
                name: 'Code review',
                description: 'Review pasted code.',
                execution_format: 'yaml',
                enabled: true,
                source: 'bundled',
              },
            ],
            invalid_package_count: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            skill_id: 'business/code-review-check',
            files: [
              { relative_path: 'skill.yaml', content: 'meta:\n  skill_id: business/code-review-check' },
              { relative_path: 'SKILL.md', content: '# Code review\nCheck the diff.' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(SkillsPanel)
    await flushPromises()
    await wrapper.get('button.skill-details-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('skill.yaml')
    expect(wrapper.text()).toContain('SKILL.md')
    expect(wrapper.text()).toContain('Check the diff.')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('uploads a selected ZIP and refreshes the catalog', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ skills: [], invalid_package_count: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            skill_id: 'legacy/meeting-notes',
            name: 'meeting-notes',
            description: 'Turn notes into minutes.',
            execution_format: 'skill_md',
            enabled: true,
            source: 'user',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            skills: [
              {
                skill_id: 'legacy/meeting-notes',
                name: 'meeting-notes',
                description: 'Turn notes into minutes.',
                execution_format: 'skill_md',
                enabled: true,
                source: 'user',
              },
            ],
            invalid_package_count: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(SkillsPanel)
    await flushPromises()
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [new File(['archive'], 'meeting-notes.zip', { type: 'application/zip' })],
    })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.text()).toContain('已导入 meeting-notes')
    expect(wrapper.text()).toContain('legacy/meeting-notes')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('toggles and deletes a user Skill after explicit confirmation', async () => {
    const userSkill = {
      skill_id: 'legacy/meeting-notes',
      name: 'meeting-notes',
      description: 'Turn notes into minutes.',
      execution_format: 'skill_md',
      enabled: true,
      source: 'user',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ skills: [userSkill], invalid_package_count: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...userSkill, enabled: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ skills: [{ ...userSkill, enabled: false }], invalid_package_count: 0 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ skills: [], invalid_package_count: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(SkillsPanel)
    await flushPromises()
    await wrapper.get('.skill-admin-actions button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已停用')
    await wrapper.get('.skill-delete-button').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('已删除')
    expect(fetchMock).toHaveBeenCalledTimes(5)
  })

  it('confirms and saves the execution mode for one dual-format Skill entry', async () => {
    const dualSkill = {
      skill_id: 'business/combined',
      name: 'Combined',
      description: 'Can use either format.',
      execution_format: 'yaml',
      detected_formats: ['yaml', 'skill_md'],
      execution_mode: 'yaml',
      available_execution_modes: ['yaml', 'deepagents', 'both'],
      enabled: true,
      source: 'user',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ skills: [dualSkill], invalid_package_count: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...dualSkill, execution_mode: 'both' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(SkillsPanel)
    await flushPromises()
    expect(wrapper.findAll('.skill-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('YAML + SKILL.md')
    await wrapper.get('select[aria-label="Combined 执行方式"]').setValue('both')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({ skill_id: 'business/combined', execution_mode: 'both' }),
    )
    expect(wrapper.text()).toContain('执行方式已更新为两种入口')
    wrapper.unmount()
  })
})
