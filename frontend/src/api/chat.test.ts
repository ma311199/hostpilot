import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  deleteConversation,
  listPendingToolOutcomes,
  listRecoverableChatTurns,
  listRecoverablePlanRuns,
  resolvePlanInvocation,
  resolveToolOutcome,
  resumePlanRun,
  upgradeConversationSandbox,
} from './chat'

describe('Conversation API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('deletes the selected conversation by encoded ID', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteConversation('conversation/one')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/conversation%2Fone',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('submits a one-way sandbox upgrade for the selected conversation', async () => {
    const summary = {
      session_id: 'conversation/one',
      title: 'Planning',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      sandbox_mode: 'danger-full-access',
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(summary), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      upgradeConversationSandbox('conversation/one', 'danger-full-access'),
    ).resolves.toEqual(summary)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/conversation%2Fone/sandbox/upgrade',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ target_mode: 'danger-full-access' }),
      }),
    )
  })

  it('lists sanitized pending tool outcomes for an encoded Session ID', async () => {
    const pending = [{
      tool_call_id: 'call-1',
      tool_name: 'write_text_file',
      created_at: '2026-11-21T10:00:00+00:00',
      state: 'pending',
    }]
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(pending), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listPendingToolOutcomes('session/one')).resolves.toEqual(pending)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/tool-outcomes',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    )
  })

  it('lists only safe recoverable Turn metadata for an encoded Session ID', async () => {
    const turns = [{
      turn_id: 'turn-1',
      started_at: '2026-11-23T00:00:00Z',
      recoverable: true,
      reason_code: null,
    }]
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(turns), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(listRecoverableChatTurns('session/one')).resolves.toEqual(turns)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/turns/recoverable',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    )
  })

  it('submits a verified result bound to the original tool call ID', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ resolved: true }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      resolveToolOutcome('session-1', 'call/1', {
        resolution: 'completed',
        result_text: 'Verified output',
      }),
    ).resolves.toEqual({ resolved: true })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/conversations/session-1/tool-outcomes/call%2F1/resolve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ resolution: 'completed', result_text: 'Verified output' }),
      }),
    )
  })

  it('lists, explicitly resumes, and resolves one encoded PlanRun', async () => {
    const planRuns = [{
      plan_run_id: 'plan/one',
      goal: 'Prepare a report',
      state: 'interrupted',
      tasks: [],
      pending_invocations: [],
      updated_at: '2026-11-22T00:00:00Z',
    }]
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(planRuns), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        plan_run_id: 'plan/one', state: 'completed', status: 'completed', reason_code: null,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ resolved: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listRecoverablePlanRuns('session/one')).resolves.toEqual(planRuns)
    await expect(resumePlanRun('session/one', 'plan/one')).resolves.toEqual({
      plan_run_id: 'plan/one', state: 'completed', status: 'completed', reason_code: null,
    })
    await expect(resolvePlanInvocation('session/one', 'plan/one', 'invocation/one', {
      resolution: 'executed', result_text: 'Observed result',
    })).resolves.toEqual({ resolved: true })

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/plan-runs/recoverable',
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/plan-runs/plan%2Fone/resume',
      'http://127.0.0.1:8000/api/v1/conversations/session%2Fone/plan-runs/plan%2Fone/invocations/invocation%2Fone/resolve',
    ])
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ method: 'POST' }))
    expect(fetchMock.mock.calls[2]?.[1]).toEqual(expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ resolution: 'executed', result_text: 'Observed result' }),
    }))
  })
})
