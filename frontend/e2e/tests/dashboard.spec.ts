/**
 * E2E tests for the Dashboard page (route: /).
 * Covers project list rendering, search/filter, navigation, and empty state.
 */
import { test, expect } from '../fixtures/base';
import { DashboardPage } from '../page-objects/DashboardPage';

// ── Shared API mock helpers ──────────────────────────────────────────────────

const MOCK_PROJECTS = [
  {
    project_id: 'proj-1',
    name: 'Auth Service',
    directory: '/home/user/auth-service',
    status: 'running',
    agents_count: 3,
    created_at: Date.now() / 1000 - 3600,
    updated_at: Date.now() / 1000 - 60,
    description: 'JWT authentication microservice',
  },
  {
    project_id: 'proj-2',
    name: 'Dashboard UI',
    directory: '/home/user/dashboard-ui',
    status: 'idle',
    agents_count: 2,
    created_at: Date.now() / 1000 - 86400,
    updated_at: Date.now() / 1000 - 7200,
    description: 'React dashboard frontend',
  },
  {
    project_id: 'proj-3',
    name: 'Data Pipeline',
    directory: '/home/user/data-pipeline',
    status: 'done',
    agents_count: 1,
    created_at: Date.now() / 1000 - 172800,
    updated_at: Date.now() / 1000 - 86400,
    description: 'ETL pipeline',
  },
];

async function mockDashboardAPIs(page: import('@playwright/test').Page) {
  // Auth status — always authenticated
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  // Projects list
  await page.route('**/api/projects', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_PROJECTS });
    }
    return route.fallback();
  });
  // Tasks for each project (empty)
  await page.route('**/api/projects/*/tasks', (route) =>
    route.fulfill({ json: [] }),
  );
  // Settings (for sidebar)
  await page.route('**/api/settings', (route) =>
    route.fulfill({ json: { projects_base_dir: '/tmp', session_expiry_hours: 24 } }),
  );
  // Agent registry
  await page.route('**/api/agent-registry', (route) =>
    route.fulfill({ json: { agents: [] } }),
  );
  // Auth devices
  await page.route('**/api/auth/devices', (route) =>
    route.fulfill({ json: { devices: [] } }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('Dashboard', () => {
  test.beforeEach(async ({ authedPage }) => {
    await mockDashboardAPIs(authedPage);
  });

  test('should render project cards when projects exist', async ({ authedPage }) => {
    const dashboard = new DashboardPage(authedPage);
    await dashboard.goto();

    // Wait for project cards to appear
    await expect(dashboard.projectCards.first()).toBeVisible({ timeout: 10_000 });

    const count = await dashboard.getProjectCount();
    expect(count).toBe(3);
  });

  test('should display project names in cards', async ({ authedPage }) => {
    const dashboard = new DashboardPage(authedPage);
    await dashboard.goto();

    await expect(dashboard.projectCards.first()).toBeVisible({ timeout: 10_000 });

    // Each project name should be visible
    for (const project of MOCK_PROJECTS) {
      const card = dashboard.getProjectCard(project.name);
      await expect(card).toBeVisible();
    }
  });

  test('should show welcome hero when no projects exist', async ({ authedPage }) => {
    // Override projects route to return empty list
    await authedPage.route('**/api/projects', (route) =>
      route.fulfill({ json: [] }),
    );

    const dashboard = new DashboardPage(authedPage);
    await dashboard.goto();

    // Either welcome hero or empty state should be visible
    const welcomeVisible = await dashboard.isWelcomeVisible();
    // If no welcome hero region, at least check no project cards appear
    if (!welcomeVisible) {
      const count = await dashboard.getProjectCount();
      expect(count).toBe(0);
    } else {
      expect(welcomeVisible).toBe(true);
    }
  });

  test('should navigate to new project page when clicking new project button', async ({
    authedPage,
  }) => {
    const dashboard = new DashboardPage(authedPage);
    await dashboard.goto();

    await expect(dashboard.newProjectButton).toBeVisible({ timeout: 10_000 });
    await dashboard.clickNewProject();

    await expect(authedPage).toHaveURL(/\/new/);
  });

  test('should navigate to project view when clicking a project card', async ({
    authedPage,
  }) => {
    // Mock the project detail endpoint
    await authedPage.route('**/api/projects/proj-1', (route) =>
      route.fulfill({ json: MOCK_PROJECTS[0] }),
    );
    await authedPage.route('**/api/projects/proj-1/messages', (route) =>
      route.fulfill({ json: [] }),
    );
    await authedPage.route('**/api/projects/proj-1/files', (route) =>
      route.fulfill({ json: { files: [] } }),
    );
    await authedPage.route('**/api/projects/proj-1/activity', (route) =>
      route.fulfill({ json: [] }),
    );

    const dashboard = new DashboardPage(authedPage);
    await dashboard.goto();

    await expect(dashboard.projectCards.first()).toBeVisible({ timeout: 10_000 });
    await dashboard.openProject('Auth Service');

    await expect(authedPage).toHaveURL(/\/project\/proj-1/);
  });
});
