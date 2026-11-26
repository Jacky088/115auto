import { defineConfig } from '@playwright/test'

const isWin = process.platform === 'win32'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
    ...(isWin ? { channel: 'msedge' } : {}),
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: 'npx vite --host 127.0.0.1 --port 4173',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 20000,
  },
})
