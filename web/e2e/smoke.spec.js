import { expect, test } from '@playwright/test'

test.describe('115AutoSave UI Smoke', () => {
  test('renders authenticated app views without console errors or horizontal overflow', async ({ page }) => {
    const errors = []
    page.on('pageerror', (err) => errors.push(err.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })

    // Mock 后端核心接口以便纯前端构建产物独立完成冒烟巡检
    await page.route('**/api/**', async (route) => {
      const url = route.request().url()
      if (url.includes('/api/auth/me')) {
        return route.fulfill({ json: { authenticated: true, setup: false, username: 'admin' } })
      }
      if (url.includes('/api/system/state')) {
        return route.fulfill({
          json: {
            running: true,
            setup_completed: true,
            paused: false,
            auto_stop_reason: '',
            space_blocked: false,
            ready: true,
            reasons: [],
          },
        })
      }
      if (url.includes('/api/version')) {
        return route.fulfill({ json: { version: '1.5.6' } })
      }
      if (url.includes('/api/dashboard/stats')) {
        return route.fulfill({
          json: {
            today_success: 12,
            today_failed: 1,
            quota: 12,
            daily_limit: 50,
            queue: 0,
            channels: 3,
            risk_115: { level: 0, max_level: 4, pace_factor: 1, cooling: false, cooldown_remaining_sec: 0 },
            categories: { all: [], '30d': [], '7d': [] },
            trend: [
              { date: '11-24', full_date: '2026-11-24', success: 8, failed: 0, blocked: 1, size_gb: 12.4 },
              { date: '11-25', full_date: '2026-11-25', success: 12, failed: 1, blocked: 0, size_gb: 18.2 },
            ],
          },
        })
      }
      if (url.includes('/api/dashboard/recent')) {
        return route.fulfill({ json: [] })
      }
      if (url.includes('/api/account/115/status')) {
        return route.fulfill({ json: { ok: true, space: { total: 1e12, use: 2e11, free: 8e11, ratio: 20 } } })
      }
      if (url.includes('/api/settings')) {
        return route.fulfill({
          json: {
            tg_mode: 'polling',
            enable_share_transfer: true,
            enable_offline_download: true,
            daily_limit: 50,
            poll_interval_min: 30,
            poll_interval_max: 120,
            transfer_interval_min: 60,
            transfer_interval_max: 180,
            retry_backoff: [60, 300, 1800],
            webhook_preset: 'wecom',
          },
        })
      }
      if (url.includes('/api/rules') || url.includes('/api/channels') || url.includes('/api/blacklist')) {
        return route.fulfill({ json: [] })
      }
      return route.fulfill({ json: {} })
    })

    for (const path of ['/dashboard', '/match', '/settings']) {
      await page.goto(path, { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(300)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(overflow, `${path} horizontal overflow`).toBeLessThanOrEqual(2)
    }

    // 验证暗黑模式切换无异常
    await page.evaluate(() => document.documentElement.classList.add('dark'))
    await page.waitForTimeout(150)
    await expect(page.locator('html')).toHaveClass(/dark/)

    expect(errors, `Console/Page errors: ${errors.join('; ')}`).toEqual([])
  })
})
