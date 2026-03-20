/**
 * E2E tests for the project creation lifecycle via NewProjectDialog (route: /new).
 * Covers form validation, field interactions, agent configuration, and submission.
 */
import { test, expect } from '../fixtures/base';

// ── Shared helpers ───────────────────────────────────────────────────────────

async function mockNewProjectAPIs(page: import('@playwright/test').Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await page.route('**/api/settings', (route) =>
    route.fulfill({
      json: {
        projects_base_dir: '/home/user/projects',
        session_expiry_hours: 24,
      },
    }),
  );
  await page.route('**/api/projects', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: [] });
    }
    return route.fallback();
  });
  await page.route('**/api/browse-dirs*', (route) =>
    route.fulfill({
      json: {
        entries: [
          { name: 'my-app', path: '/home/user/projects/my-app', is_git: true },
          { name: 'docs', path: '/home/user/projects/docs', is_git: false },
        ],
        current: '/home/user/projects',
        parent: '/home/user',
        home: '/home/user',
      },
    }),
  );
  await page.route('**/api/agent-registry', (route) =>
    route.fulfill({ json: { agents: [] } }),
  );
  await page.route('**/api/auth/devices', (route) =>
    route.fulfill({ json: { devices: [] } }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('Project Lifecycle — NewProjectDialog', () => {
  test.beforeEach(async ({ authedPage }) => {
    await mockNewProjectAPIs(authedPage);
  });

  test('should render the new project form with required fields', async ({ authedPage }) => {
    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    // Title
    await expect(authedPage.locator('h1').filter({ hasText: /New Project/i })).toBeVisible();

    // Required fields
    const nameInput = authedPage.locator('input[placeholder*="my-awesome"]');
    await expect(nameInput).toBeVisible();

    const dirInput = authedPage.locator('input[placeholder*="projects"]');
    await expect(dirInput).toBeVisible();
  });

  test('should show validation error when creating with empty name', async ({ authedPage }) => {
    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    // Fill directory but leave name empty
    const dirInput = authedPage.locator('input[placeholder*="projects"]');
    await dirInput.fill('/home/user/projects/test');

    // Click create
    const createBtn = authedPage.getByRole('button', { name: /Create Project/i });
    await createBtn.click();

    // Should show error about name being required
    await expect(authedPage.locator('text=Project name is required')).toBeVisible();
  });

  test('should auto-fill directory from base_dir when typing project name', async ({
    authedPage,
  }) => {
    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    // Type project name
    const nameInput = authedPage.locator('input[placeholder*="my-awesome"]');
    await nameInput.fill('My Cool App');

    // Directory should be auto-populated based on base_dir + slugified name
    const dirInput = authedPage.locator('input[placeholder*="projects"]');
    await expect(dirInput).toHaveValue(/my-cool-app/);
  });

  test('should allow selecting agent configuration', async ({ authedPage }) => {
    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    // Default should be "Team" (agentsCount=2)
    const teamBtn = authedPage.getByRole('button', { name: /Team/i }).first();
    await expect(teamBtn).toBeVisible();

    // Click "Full Team"
    const fullTeamBtn = authedPage.getByRole('button', { name: /Full Team/i });
    await fullTeamBtn.click();

    // Agent swarm preview should show more agents
    // The swarm preview renders agent circles with title attributes
    const agentCircles = authedPage.locator('[title]').filter({ hasText: /^(PM|FE|BE|DB|QA|DV|SE|AI|OPS|UX|AR|RV)$/ });
    const count = await agentCircles.count();
    expect(count).toBeGreaterThanOrEqual(10); // Full team = 12 agents
  });

  test('should submit project creation and navigate to project view', async ({
    authedPage,
  }) => {
    // Mock successful creation
    await authedPage.route('**/api/projects', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          json: { project_id: 'new-proj-42', status: 'created' },
        });
      }
      return route.fulfill({ json: [] });
    });
    // Mock project detail for navigation target
    await authedPage.route('**/api/projects/new-proj-42', (route) =>
      route.fulfill({
        json: {
          project_id: 'new-proj-42',
          name: 'Test Project',
          status: 'idle',
          directory: '/tmp/test',
        },
      }),
    );
    await authedPage.route('**/api/projects/new-proj-42/messages', (route) =>
      route.fulfill({ json: [] }),
    );
    await authedPage.route('**/api/projects/new-proj-42/files', (route) =>
      route.fulfill({ json: { files: [] } }),
    );
    await authedPage.route('**/api/projects/new-proj-42/activity', (route) =>
      route.fulfill({ json: [] }),
    );
    await authedPage.route('**/api/projects/new-proj-42/tasks', (route) =>
      route.fulfill({ json: [] }),
    );

    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    // Fill name
    const nameInput = authedPage.locator('input[placeholder*="my-awesome"]');
    await nameInput.fill('Test Project');

    // Fill directory
    const dirInput = authedPage.locator('input[placeholder*="projects"]');
    await dirInput.fill('/tmp/test');

    // Submit
    const createBtn = authedPage.getByRole('button', { name: /Create Project/i });
    await createBtn.click();

    // Should navigate to the new project's page
    await expect(authedPage).toHaveURL(/\/project\/new-proj-42/, { timeout: 10_000 });
  });

  test('should cancel and navigate back to dashboard', async ({ authedPage }) => {
    await authedPage.goto('/new');
    await authedPage.waitForLoadState('networkidle');

    const cancelBtn = authedPage.getByRole('button', { name: /Cancel/i });
    await cancelBtn.click();

    await expect(authedPage).toHaveURL(/^\/$|\/$/);
  });
});
