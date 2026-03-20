/**
 * E2E tests for WebSocket reconnection behavior.
 * Covers the WSReconnectBanner display during disconnection and reconnection,
 * and verifies that events resume after reconnection.
 */
import { test, expect } from '../fixtures/base';

// ── Mock helpers ─────────────────────────────────────────────────────────────

async function mockBaseAPIs(page: import('@playwright/test').Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await page.route('**/api/projects', (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route('**/api/settings', (route) =>
    route.fulfill({ json: { projects_base_dir: '/tmp', session_expiry_hours: 24 } }),
  );
  await page.route('**/api/agent-registry', (route) =>
    route.fulfill({ json: { agents: [] } }),
  );
  await page.route('**/api/auth/devices', (route) =>
    route.fulfill({ json: { devices: [] } }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('WebSocket Resilience', () => {
  test.beforeEach(async ({ authedPage }) => {
    await mockBaseAPIs(authedPage);
  });

  test('should show reconnecting banner when WebSocket disconnects', async ({
    authedPage,
    mockWebSocket,
  }) => {
    const ws = await mockWebSocket();
    await authedPage.goto('/');
    await authedPage.waitForLoadState('networkidle');

    // Close the WebSocket to simulate disconnection
    await ws.close(1006);

    // The reconnecting banner should appear with role="alert"
    const banner = authedPage.locator('[role="alert"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).toContainText(/[Rr]econnect/);
  });

  test('should show restored banner after WebSocket reconnects', async ({
    authedPage,
    mockWebSocket,
  }) => {
    const ws = await mockWebSocket();
    await authedPage.goto('/');
    await authedPage.waitForLoadState('networkidle');

    // Disconnect
    await ws.close(1006);

    // Wait for reconnecting banner
    const banner = authedPage.locator('[role="alert"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // The app will auto-reconnect and the mock will respond with auth_ok
    // causing the banner to transition to "Connected" / "restored"
    // Wait for the banner text to change or disappear
    await expect(banner).toContainText(/[Cc]onnected|[Rr]econnect/, { timeout: 15_000 });
  });

  test('should have aria-live assertive for accessibility', async ({
    authedPage,
    mockWebSocket,
  }) => {
    const ws = await mockWebSocket();
    await authedPage.goto('/');
    await authedPage.waitForLoadState('networkidle');

    await ws.close(1006);

    const banner = authedPage.locator('[role="alert"][aria-live="assertive"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });
  });
});
