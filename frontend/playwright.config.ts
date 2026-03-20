import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E test configuration for Hivemind Dashboard.
 *
 * - Uses the Vite dev server (port 5173) as the base URL.
 * - Chromium headless by default; run with `--headed` for debug mode.
 * - WebServer config auto-starts `npm run dev` before tests.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // Sequential to avoid WebSocket state conflicts
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker for WS state isolation
  reporter: process.env.CI
    ? [['html', { open: 'never' }], ['github']]
    : [['html', { open: 'on-failure' }]],

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    port: 5173,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
