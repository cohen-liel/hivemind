/**
 * E2E tests for the Settings page (route: /settings).
 * Covers settings rendering, field editing, save, and device management.
 */
import { test, expect } from '../fixtures/base';

// ── Mock data ────────────────────────────────────────────────────────────────

const MOCK_SETTINGS = {
  max_turns_per_cycle: 50,
  max_budget_usd: 10.0,
  agent_timeout_seconds: 300,
  max_orchestrator_loops: 10,
  sdk_max_turns_per_query: 25,
  sdk_max_budget_per_query: 5.0,
  max_user_message_length: 10000,
  session_expiry_hours: 24,
  projects_base_dir: '/home/user/projects',
};

const MOCK_DEVICES = {
  devices: [
    {
      device_id: 'dev-001',
      name: 'MacBook Pro',
      approved_at: Date.now() / 1000 - 86400,
      last_seen: Date.now() / 1000 - 60,
      ip: '192.168.1.10',
      user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    },
  ],
};

async function mockSettingsAPIs(page: import('@playwright/test').Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await page.route('**/api/settings', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_SETTINGS });
    }
    // PUT/PATCH — save
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/settings/persist', (route) =>
    route.fulfill({ json: { ok: true } }),
  );
  await page.route('**/api/auth/devices', (route) =>
    route.fulfill({ json: MOCK_DEVICES }),
  );
  await page.route('**/api/projects', (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route('**/api/agent-registry', (route) =>
    route.fulfill({ json: { agents: [] } }),
  );
  // Watchdog status
  await page.route('**/api/watchdog/**', (route) =>
    route.fulfill({ json: { status: 'ok' } }),
  );
  await page.route('**/api/watchdog*', (route) =>
    route.fulfill({ json: { status: 'ok' } }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('Settings Page', () => {
  test.beforeEach(async ({ authedPage }) => {
    await mockSettingsAPIs(authedPage);
  });

  test('should render the settings page heading', async ({ authedPage }) => {
    await authedPage.goto('/settings');
    await authedPage.waitForLoadState('networkidle');

    await expect(authedPage.locator('h1').filter({ hasText: /Settings/i })).toBeVisible();
  });

  test('should display editable settings fields with current values', async ({ authedPage }) => {
    await authedPage.goto('/settings');
    await authedPage.waitForLoadState('networkidle');

    // Check some known fields exist with their current values
    const maxTurnsInput = authedPage.getByLabel('Max Turns per Cycle');
    await expect(maxTurnsInput).toBeVisible();
    await expect(maxTurnsInput).toHaveValue('50');

    const budgetInput = authedPage.getByLabel('Max Budget (USD)');
    await expect(budgetInput).toBeVisible();
    await expect(budgetInput).toHaveValue('10');
  });

  test('should show save button when settings are modified', async ({ authedPage }) => {
    await authedPage.goto('/settings');
    await authedPage.waitForLoadState('networkidle');

    // Initially no save button
    const saveBtn = authedPage.getByRole('button', { name: /Save Changes/i });
    await expect(saveBtn).not.toBeVisible();

    // Modify a setting
    const maxTurnsInput = authedPage.getByLabel('Max Turns per Cycle');
    await maxTurnsInput.fill('100');

    // Save button should appear
    await expect(saveBtn).toBeVisible();
  });

  test('should display approved devices section', async ({ authedPage }) => {
    await authedPage.goto('/settings');
    await authedPage.waitForLoadState('networkidle');

    // Approved devices heading
    await expect(authedPage.locator('text=Approved Devices')).toBeVisible();

    // Device entry should show
    await expect(authedPage.locator('text=192.168.1.10')).toBeVisible();
  });

  test('should show error state when settings API fails', async ({ authedPage }) => {
    // Override settings to fail
    await authedPage.route('**/api/settings', (route) =>
      route.fulfill({ status: 500, json: { error: 'Internal error' } }),
    );

    await authedPage.goto('/settings');
    await authedPage.waitForLoadState('networkidle');

    // Should show error state or connection error message
    const errorVisible = await authedPage.locator('text=/could not load|backend|error|retry/i').isVisible();
    expect(errorVisible).toBe(true);
  });
});
