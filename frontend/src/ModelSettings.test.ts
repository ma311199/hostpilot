import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ModelSettings from './ModelSettings.vue'
import {
  getModelConfiguration,
  saveModelConfiguration,
  testModelConnection,
} from './api/model'
import { getRoutingModelConfiguration, saveRoutingModelConfiguration } from './api/routingModel'

vi.mock('./api/model', () => ({
  getModelConfiguration: vi.fn(),
  saveModelConfiguration: vi.fn(),
  testModelConnection: vi.fn(),
}))

vi.mock('./api/routingModel', () => ({
  getRoutingModelConfiguration: vi.fn(),
  saveRoutingModelConfiguration: vi.fn(),
}))

describe('model settings', () => {
  beforeEach(() => {
    vi.mocked(getRoutingModelConfiguration).mockResolvedValue({
      configured: false,
      saved: false,
      judge_api_key_configured: false,
      embedding_api_key_configured: false,
      reranker_api_key_configured: false,
      judge_model: null,
      embedding_model: null,
      reranker_model: null,
      judge_base_url: null,
      embedding_base_url: null,
      reranker_base_url: null,
    })
    vi.mocked(saveRoutingModelConfiguration).mockResolvedValue({
      configured: true,
      saved: true,
      judge_api_key_configured: true,
      embedding_api_key_configured: true,
      reranker_api_key_configured: true,
      judge_model: 'small-model',
      embedding_model: 'embedding-model',
      reranker_model: 'reranker-model',
      judge_base_url: 'https://light.example/v1',
      embedding_base_url: 'https://embed.example/v1',
      reranker_base_url: 'https://rerank.example/v1',
    })
    vi.mocked(getModelConfiguration).mockResolvedValue({
      configured: false,
      saved: false,
      api_key_required: false,
      model_name: null,
      base_url: null,
      streaming: true,
    })
    vi.mocked(saveModelConfiguration).mockResolvedValue({
      configured: true,
      saved: true,
      api_key_required: false,
      model_name: 'gpt-6-luna',
      base_url: 'https://proxy.example/v1',
      streaming: true,
    })
    vi.mocked(testModelConnection).mockResolvedValue({
      role: 'embedding',
      success: true,
      message: '连接成功，模型已返回有效响应。',
      error_code: null,
    })
  })

  it('enables streaming by default and submits that setting', async () => {
    const wrapper = mount(ModelSettings)
    await flushPromises()

    const streaming = wrapper.get('input[type="checkbox"]')
    expect((streaming.element as HTMLInputElement).checked).toBe(true)
    await wrapper.get('input[placeholder="例如 gpt-4.1-mini"]').setValue('gpt-6-luna')
    await wrapper.get('input[type="password"]').setValue('test-key')
    await wrapper.get('input[type="url"]').setValue('https://proxy.example/v1')
    await wrapper.findAll('.model-role-card')[0].get('.model-save-button').trigger('click')
    await flushPromises()

    expect(saveModelConfiguration).toHaveBeenCalledWith({
      model_name: 'gpt-6-luna',
      api_key: 'test-key',
      base_url: 'https://proxy.example/v1',
      streaming: true,
    })
    expect(wrapper.text()).toContain('LLM Model已保存并启用')
    wrapper.unmount()
  })

  it('restores saved options and only requires the API key after restart', async () => {
    vi.mocked(getModelConfiguration).mockResolvedValue({
      configured: false,
      saved: true,
      api_key_required: true,
      model_name: 'persisted-model',
      base_url: 'https://models.example/v1',
      streaming: false,
    })

    const wrapper = mount(ModelSettings)
    await flushPromises()

    expect((wrapper.get('input[placeholder="例如 gpt-4.1-mini"]').element as HTMLInputElement).value)
      .toBe('persisted-model')
    expect((wrapper.get('input[type="url"]').element as HTMLInputElement).value)
      .toBe('https://models.example/v1')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).required).toBe(true)
    expect(wrapper.get('input[type="password"]').attributes('placeholder')).toContain('LLM Model 服务商')
    wrapper.unmount()
  })

  it('saves one optional model role without changing the other roles', async () => {
    const wrapper = mount(ModelSettings)
    await flushPromises()

    await wrapper.get('input[placeholder="例如 gpt-4.1-mini"]').setValue('main-model')
    await wrapper.get('input[placeholder="留空时使用主 LLM"]').setValue('light-model')
    await wrapper.get('input[placeholder="留空时使用 BM25 词项召回"]').setValue('embed-model')
    await wrapper.get('input[placeholder="未配置时跳过重排"]').setValue('rerank-model')
    const roleKeys = wrapper.findAll('input[type="password"]')
    await roleKeys[1].setValue('light-key')
    await roleKeys[2].setValue('embedding-key')
    await roleKeys[3].setValue('reranker-key')
    const roleUrls = wrapper.findAll('input[type="url"]')
    await roleUrls[1].setValue('https://light.example/v1')
    await roleUrls[2].setValue('https://embed.example/v1')
    await roleUrls[3].setValue('https://rerank.example/v1')
    await wrapper.findAll('.model-role-card')[2].get('.model-save-button').trigger('click')
    await flushPromises()

    expect(saveRoutingModelConfiguration).toHaveBeenCalledWith({
      embedding_model: 'embed-model',
      embedding_api_key: 'embedding-key',
      embedding_base_url: 'https://embed.example/v1',
    })
    wrapper.unmount()
  })

  it('tests the draft connection without saving it', async () => {
    const wrapper = mount(ModelSettings)
    await flushPromises()
    const embedding = wrapper.findAll('.model-role-card')[2]

    await embedding.get('input[placeholder="留空时使用 BM25 词项召回"]').setValue('embed-model')
    await embedding.get('input[placeholder="Embedding API Key；本机持久保存"]').setValue('embed-key')
    await embedding.get('.model-test-button').trigger('click')
    await flushPromises()

    expect(testModelConnection).toHaveBeenCalledWith({
      role: 'embedding',
      model_name: 'embed-model',
      api_key: 'embed-key',
      base_url: null,
    })
    expect(saveRoutingModelConfiguration).not.toHaveBeenCalled()
    expect(embedding.text()).toContain('连接成功')
    wrapper.unmount()
  })

  it('keeps the dialog open when text selection starts in the model name field', async () => {
    const wrapper = mount(ModelSettings)
    await flushPromises()

    await wrapper.get('input[placeholder="例如 gpt-4.1-mini"]').trigger('pointerdown')
    await wrapper.get('.model-settings-backdrop').trigger('click')

    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('closes when the user presses the backdrop directly', async () => {
    const wrapper = mount(ModelSettings)
    await flushPromises()

    await wrapper.get('.model-settings-backdrop').trigger('pointerdown')

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
