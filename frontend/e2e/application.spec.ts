import { expect, test } from '@playwright/test'

test('persists Harness settings and opens the management pages', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status').filter({ hasText: '后端在线' })).toBeVisible()

  await page.getByRole('button', { name: 'Harness 配置' }).click()
  await expect(page.getByRole('heading', { name: 'Harness 配置' })).toBeVisible()

  const tokenBudget = page.getByRole('spinbutton', { name: 'Token 预算' })
  await expect(tokenBudget).toHaveValue('20000')
  await tokenBudget.fill('12345')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Harness 配置已保存' })).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Harness 配置' }).click()
  await expect(page.getByRole('spinbutton', { name: 'Token 预算' })).toHaveValue('12345')

  await page.getByRole('button', { name: 'MCP 连接' }).click()
  await expect(page.getByRole('heading', { name: 'MCP 连接' })).toBeVisible()
  const parameterMode = page.getByRole('button', { name: '参数配置', exact: true })
  const jsonMode = page.getByRole('button', { name: 'JSON 配置', exact: true })
  await expect(parameterMode).toHaveAttribute('aria-pressed', 'true')
  await jsonMode.click()
  await expect(jsonMode).toHaveAttribute('aria-pressed', 'true')
  await expect(parameterMode).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('form', { name: '导入 MCP Server JSON' })).toBeVisible()

  await page.getByRole('button', { name: 'Skill 库' }).click()
  await expect(page.getByRole('heading', { name: 'Skill 库' })).toBeVisible()

  await page.getByRole('button', { name: 'Trace' }).click()
  await expect(page.getByRole('heading', { name: 'Trace 观测' })).toBeVisible()

  await page.getByRole('button', { name: 'Audit' }).click()
  await expect(page.getByRole('heading', { name: 'Audit 审计' })).toBeVisible()
})

test('loads a stored Trace and displays only allowlisted event metadata', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Trace' }).click()
  const traceCard = page.locator('.trace-card').first()
  await expect(traceCard).toContainText('chat.turn')
  await traceCard.click()

  const traceDetail = page.getByRole('region', { name: 'Trace 详情' })
  await expect(traceDetail.getByText('chat.turn')).toBeVisible()
  await expect(traceDetail.getByText('completed', { exact: true }).first()).toBeVisible()
  await expect(traceDetail.getByText('direct', { exact: true })).toBeVisible()
  await expect(traceDetail).not.toContainText('private E2E fixture content')
})

test('imports MCP JSON and reconnects to the saved Server after reload', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'MCP 连接' }).click()
  await page.getByRole('button', { name: 'JSON 配置', exact: true }).click()

  await page.getByRole('textbox', { name: '从 JSON 导入 MCP Server' }).fill(
    JSON.stringify({
      mcpServers: {
        e2e_local: {
          url: 'http://127.0.0.1:8012/mcp',
          timeoutSeconds: 5,
        },
      },
    }),
  )
  await page.getByRole('button', { name: '导入 JSON 配置' }).click()
  await expect(page.getByRole('status').filter({ hasText: '已导入 1 个 MCP Server 配置' })).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'MCP 连接' }).click()
  const restoredServerRow = page.locator('.server-row').filter({ hasText: 'e2e_local' })
  await expect(restoredServerRow).toContainText('http://127.0.0.1:8012/mcp')
  await expect(restoredServerRow).toContainText('已连接')
  await expect(restoredServerRow).toContainText('发现工具 1')
})

test('toggles a user Skill and restores its state after page reload', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Skill 库' }).click()

  let skillCard = page.locator('.skill-card').filter({ hasText: 'business/e2e-toggle' })
  await expect(skillCard).toContainText('已启用')
  await skillCard.getByRole('button', { name: '停用' }).click()
  await expect(skillCard).toContainText('已停用')
  await expect(skillCard.getByRole('button', { name: '启用' })).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Skill 库' }).click()
  skillCard = page.locator('.skill-card').filter({ hasText: 'business/e2e-toggle' })
  await expect(skillCard).toContainText('已停用')
  await skillCard.getByRole('button', { name: '启用' }).click()
  await expect(skillCard).toContainText('已启用')

  page.once('dialog', (dialog) => dialog.dismiss())
  await skillCard.getByRole('button', { name: '删除' }).click()
  await expect(skillCard).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await skillCard.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.skill-card').filter({ hasText: 'business/e2e-toggle' })).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: 'E2E Toggle Skill 已删除' })).toBeVisible()
})

test('selects a workspace through the injected native-picker boundary and restores it', async ({ page }) => {
  const selectedWorkspace = process.env.HOSTPILOT_E2E_WORKSPACE
  if (!selectedWorkspace) throw new Error('HOSTPILOT_E2E_WORKSPACE is not configured.')

  await page.goto('/')
  await page.getByRole('button', { name: '聊天' }).click()
  await page.getByRole('button', { name: '选择目录' }).click()

  const workspacePath = page.locator('.workspace-path')
  await expect(workspacePath).toContainText(selectedWorkspace)

  await page.reload()
  await page.getByRole('button', { name: '聊天' }).click()
  const restoredWorkspacePath = page.locator('.workspace-path')
  await expect(restoredWorkspacePath).toContainText('selected-workspace')

  await page.getByRole('button', { name: '选择目录' }).click()
  await expect(restoredWorkspacePath).not.toContainText('selected-workspace')
  await expect(restoredWorkspacePath).not.toContainText('只读')
})

test('confirms conversation deletion and switches away from the deleted active conversation', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', { name: '聊天' }).click()

  await page.getByRole('button', { name: '新建' }).click()
  await expect(page.locator('.conversation-row')).toHaveCount(1)
  const firstConversation = (await (await page.request.get(
    'http://127.0.0.1:8011/api/v1/conversations',
  )).json()) as Array<{ conversation_id: string }>

  await page.getByRole('button', { name: '新建' }).click()
  await expect(page.locator('.conversation-row')).toHaveCount(2)
  const conversations = (await (await page.request.get(
    'http://127.0.0.1:8011/api/v1/conversations',
  )).json()) as Array<{ conversation_id: string }>
  expect(conversations).toHaveLength(2)

  const activeRow = page.locator('.conversation-row').first()
  page.once('dialog', (dialog) => dialog.dismiss())
  await activeRow.getByRole('button', { name: /^删除会话/ }).click()
  await expect(page.locator('.conversation-row')).toHaveCount(2)

  page.once('dialog', (dialog) => dialog.accept())
  await activeRow.getByRole('button', { name: /^删除会话/ }).click()
  await expect(page.locator('.conversation-row')).toHaveCount(1)
  await expect(page.locator('.conversation-item-active')).toHaveCount(1)

  const remaining = (await (await page.request.get(
    'http://127.0.0.1:8011/api/v1/conversations',
  )).json()) as Array<{ conversation_id: string }>
  expect(remaining).toHaveLength(1)
  expect(remaining[0].conversation_id).toBe(firstConversation[0].conversation_id)
  const deletedResponse = await page.request.get(
    `http://127.0.0.1:8011/api/v1/conversations/${conversations[0].conversation_id}`,
  )
  expect(deletedResponse.status()).toBe(404)
})

test('requires confirmation and persists irreversible sandbox upgrades', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '聊天' }).click()
  await page.getByRole('button', { name: '新建' }).click()

  const sandboxControls = page.locator('.sandbox-controls')
  await expect(sandboxControls).toHaveAttribute('data-mode', 'read-only')

  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: '升级到工作区可写' }).click()
  await expect(sandboxControls).toHaveAttribute('data-mode', 'read-only')

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '升级到工作区可写' }).click()
  await expect(sandboxControls).toHaveAttribute('data-mode', 'workspace-write')

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '升级到完全访问' }).click()
  await expect(sandboxControls).toHaveAttribute('data-mode', 'danger-full-access')

  await page.reload()
  const conversationsResponse = await page.request.get('http://127.0.0.1:8011/api/v1/conversations')
  const conversations = (await conversationsResponse.json()) as Array<{
    conversation_id: string
    sandbox_mode: string
  }>
  expect(conversations[0].sandbox_mode).toBe('danger-full-access')
  const downgradeResponse = await page.request.post(
    `http://127.0.0.1:8011/api/v1/conversations/${conversations[0].conversation_id}/sandbox/upgrade`,
    { data: { target_mode: 'workspace-write' } },
  )
  expect(downgradeResponse.status()).toBe(409)
})
