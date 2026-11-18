import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { expect, test } from '@playwright/test'

test('sends approval, rejection, and cancellation decisions through the chat WebSocket', async ({
  page,
}) => {
  const workspace = process.env.HOSTPILOT_E2E_WORKSPACE
  if (!workspace) throw new Error('HOSTPILOT_E2E_WORKSPACE is not configured.')

  await page.goto('/')
  await page.getByRole('button', { name: '聊天' }).click()
  await page.getByRole('button', { name: '选择目录' }).click()
  await expect(page.locator('.workspace-path')).toContainText(workspace)

  await startWritableConversation(page)
  await sendApprovalRequest(page, 'Request an isolated file write.')

  const approvalDialog = page.getByRole('dialog', { name: '确认工具操作' })
  await expect(approvalDialog).toContainText('write_text_file')
  await expect(approvalDialog).toContainText('approval-e2e-1.txt')
  await expect(approvalDialog).not.toContainText('isolated approval content')
  await approvalDialog.getByRole('button', { name: '批准并执行' }).click()
  await expect(page.getByText(/approval-e2e-1\.txt/)).toBeVisible()
  await expect(readFile(join(workspace, 'approval-e2e-1.txt'), 'utf8'))
    .resolves.toBe('isolated approval content 1')

  await startWritableConversation(page)
  await sendApprovalRequest(page, 'Request a file write that should be rejected.')
  const rejectionDialog = page.getByRole('dialog', { name: '确认工具操作' })
  await expect(rejectionDialog).toContainText('approval-e2e-2.txt')
  await page.getByRole('button', { name: '拒绝', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('你已拒绝此操作')
  await expect(readFile(join(workspace, 'approval-e2e-2.txt'), 'utf8')).rejects.toThrow()

  await startWritableConversation(page)
  await sendApprovalRequest(page, 'Request a file write that should be cancelled.')
  const cancellationDialog = page.getByRole('dialog', { name: '确认工具操作' })
  await expect(cancellationDialog).toContainText('approval-e2e-3.txt')
  await page.getByRole('button', { name: '取消本次请求' }).click()
  await expect(page.getByRole('alert')).toContainText('你已取消此操作')
  await expect(readFile(join(workspace, 'approval-e2e-3.txt'), 'utf8')).rejects.toThrow()
})

test('requires approval before running Python in the isolated sandbox', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '聊天' }).click()
  const toolsResponse = await page.request.get('http://127.0.0.1:8013/api/v1/tools')
  const catalog = (await toolsResponse.json()) as {
    tools: Array<{ name: string; configured: boolean }>
  }
  const pythonTool = catalog.tools.find((tool) => tool.name === 'run_python_code')
  test.skip(!pythonTool?.configured, 'Linux bubblewrap Python sandbox is unavailable.')

  await page.getByRole('textbox', { name: '输入消息' }).fill('Run the sandbox approval check rejected.')
  await page.locator('.chat-composer').getByRole('button', { name: /发送/ }).click()
  const rejectedDialog = page.getByRole('dialog', { name: '确认工具操作' })
  await expect(rejectedDialog).toContainText('run_python_code')
  await expect(rejectedDialog).toContainText('无网络和主机文件访问')
  await expect(rejectedDialog).not.toContainText('sandbox-rejected-output')
  await page.getByRole('button', { name: '拒绝', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('本次操作未执行')
  await expect(page.locator('.message-row')).toHaveCount(0)

  await page.getByRole('textbox', { name: '输入消息' }).fill('Run the sandbox approval check approved.')
  await page.locator('.chat-composer').getByRole('button', { name: /发送/ }).click()
  const approvedDialog = page.getByRole('dialog', { name: '确认工具操作' })
  await expect(approvedDialog).toContainText('run_python_code')
  await page.getByRole('button', { name: '批准并执行' }).click()
  await expect(page.locator('.message-row.message-assistant').last())
    .toContainText('sandbox-approved-output')
})

async function startWritableConversation(page: import('@playwright/test').Page): Promise<void> {
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '新建' }).click()
  await page.getByRole('button', { name: '升级到工作区可写' }).click()
  await expect(page.locator('.sandbox-controls')).toHaveAttribute('data-mode', 'workspace-write')
}

async function sendApprovalRequest(
  page: import('@playwright/test').Page,
  message: string,
): Promise<void> {
  await page.getByRole('textbox', { name: '输入消息' }).fill(message)
  await page.locator('.chat-composer').getByRole('button', { name: /发送/ }).click()
}
