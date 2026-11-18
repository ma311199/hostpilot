import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PaginationControls from './PaginationControls.vue'

describe('PaginationControls', () => {
  it('hides when one page or fewer is needed', () => {
    expect(mount(PaginationControls, { props: { page: 1, pageSize: 12, total: 0 } }).find('nav').exists()).toBe(false)
    expect(mount(PaginationControls, { props: { page: 1, pageSize: 12, total: 12 } }).find('nav').exists()).toBe(false)
  })

  it('shows range and emits one-based page changes', async () => {
    const wrapper = mount(PaginationControls, { props: { page: 2, pageSize: 12, total: 25 } })
    expect(wrapper.text()).toContain('13–24 / 25')
    expect(wrapper.find('[aria-current="page"]').text()).toBe('2')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(wrapper.emitted('update:page')?.[0]).toEqual([3])
    await wrapper.get('button[aria-label="上一页"]').trigger('click')
    expect(wrapper.emitted('update:page')?.[1]).toEqual([1])
  })

  it('disables controls at the ends', () => {
    const first = mount(PaginationControls, { props: { page: 1, pageSize: 12, total: 25 } })
    const last = mount(PaginationControls, { props: { page: 3, pageSize: 12, total: 25 } })
    expect(first.get('button[aria-label="上一页"]').attributes('disabled')).toBeDefined()
    expect(last.get('button[aria-label="下一页"]').attributes('disabled')).toBeDefined()
  })
})
