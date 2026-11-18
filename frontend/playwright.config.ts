import { cpSync, mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'

import { defineConfig, devices } from '@playwright/test'

const apiPort = 8011
const webPort = 5174
const webOrigin = `http://127.0.0.1:${webPort}`
const apiBaseUrl = `http://127.0.0.1:${apiPort}/api/v1`
const e2eRoot = process.env.HOSTPILOT_E2E_ROOT || mkdtempSync(`${tmpdir()}/hostpilot-e2e-`)
const e2eWorkspace = `${e2eRoot}/selected-workspace`
const projectRoot = resolve(import.meta.dirname, '..')

cpSync(resolve(import.meta.dirname, 'e2e/fixtures/skills'), `${e2eRoot}/skills`, {
  recursive: true,
})
mkdirSync(e2eWorkspace, { recursive: true })
process.env.HOSTPILOT_E2E_ROOT = e2eRoot
process.env.HOSTPILOT_E2E_WORKSPACE = e2eWorkspace
process.env.PLAYWRIGHT_BROWSERS_PATH ??= `${tmpdir()}/hostpilot-playwright-browsers`

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/application.spec.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  outputDir: '/tmp/hostpilot-playwright-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: webOrigin,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: '.venv/bin/python frontend/e2e/fixtures/api_server.py',
      cwd: projectRoot,
      url: `${apiBaseUrl}/harness/configuration`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        HOSTPILOT_API_HOST: '127.0.0.1',
        HOSTPILOT_API_PORT: String(apiPort),
        HOSTPILOT_DATA_DIR: `${e2eRoot}/data`,
        HOSTPILOT_MEMORY_DIR: `${e2eRoot}/memory`,
        HOSTPILOT_SKILLS_DIR: `${e2eRoot}/skills`,
        HOSTPILOT_WEB_ORIGINS: JSON.stringify([webOrigin]),
        HOSTPILOT_E2E_WORKSPACE: e2eWorkspace,
      },
    },
    {
      command: `pnpm exec vite --host 127.0.0.1 --port ${webPort} --strictPort`,
      cwd: resolve(import.meta.dirname),
      url: webOrigin,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        VITE_HOSTPILOT_API_BASE_URL: apiBaseUrl,
        VITE_HOSTPILOT_WS_URL: `ws://127.0.0.1:${apiPort}/api/v1/ws/status`,
        VITE_HOSTPILOT_CHAT_WS_URL: `ws://127.0.0.1:${apiPort}/api/v1/ws/chat`,
      },
    },
    {
      command: '.venv/bin/python frontend/e2e/fixtures/mcp_server.py',
      cwd: projectRoot,
      url: 'http://127.0.0.1:8012/health',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
