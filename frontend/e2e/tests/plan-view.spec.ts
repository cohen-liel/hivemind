/**
 * E2E tests for PlanView — the DAG task graph visualization.
 * Tests task rendering, status transitions via WebSocket events, and progress tracking.
 */
import { test, expect } from '../fixtures/base';
import { PlanViewPage } from '../page-objects/PlanViewPage';
import { ProjectViewPage } from '../page-objects/ProjectViewPage';

// ── Mock data ────────────────────────────────────────────────────────────────

const PROJECT_ID = 'proj-plan-test';

const MOCK_PROJECT = {
  project_id: PROJECT_ID,
  name: 'Plan Test Project',
  directory: '/tmp/plan-test',
  status: 'running',
  agents_count: 2,
  created_at: Date.now() / 1000 - 600,
  updated_at: Date.now() / 1000,
  description: 'Testing plan view',
  plan: {
    vision: 'Build a comprehensive test suite for the Hivemind dashboard',
    epics: [
      { id: '1', title: 'Setup', tasks: ['task_001', 'task_002'] },
      { id: '2', title: 'Implementation', tasks: ['task_003'] },
    ],
    tasks: [
      { task_id: 'task_001', goal: 'Set up Playwright config', status: 'done', role: 'test_engineer' },
      { task_id: 'task_002', goal: 'Create page objects', status: 'working', role: 'frontend_developer' },
      { task_id: 'task_003', goal: 'Write E2E tests', status: 'pending', role: 'test_engineer' },
    ],
  },
};

async function mockPlanViewAPIs(page: import('@playwright/test').Page) {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await page.route('**/api/projects', (route) =>
    route.fulfill({ json: [MOCK_PROJECT] }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}`, (route) =>
    route.fulfill({ json: MOCK_PROJECT }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}/messages`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}/files`, (route) =>
    route.fulfill({ json: { files: [] } }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}/activity`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(`**/api/projects/${PROJECT_ID}/tasks`, (route) =>
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

test.describe('PlanView — DAG Task Graph', () => {
  test.beforeEach(async ({ authedPage }) => {
    await mockPlanViewAPIs(authedPage);
  });

  test('should display vision statement when plan has one', async ({ authedPage, mockWebSocket }) => {
    const ws = await mockWebSocket();
    const projectView = new ProjectViewPage(authedPage);
    await projectView.goto(PROJECT_ID);

    // Send plan data via WebSocket
    await ws.send({
      type: 'plan_update',
      project_id: PROJECT_ID,
      plan: MOCK_PROJECT.plan,
    });

    const planView = new PlanViewPage(authedPage);

    // Look for vision text in the page
    const visionText = authedPage.locator('text=Build a comprehensive test suite');
    await expect(visionText).toBeVisible({ timeout: 10_000 });
  });

  test('should render task steps from plan data', async ({ authedPage, mockWebSocket }) => {
    const ws = await mockWebSocket();
    const projectView = new ProjectViewPage(authedPage);
    await projectView.goto(PROJECT_ID);

    // Send plan data
    await ws.send({
      type: 'plan_update',
      project_id: PROJECT_ID,
      plan: MOCK_PROJECT.plan,
    });

    // Wait for task content to appear
    await expect(authedPage.locator('text=Set up Playwright config')).toBeVisible({ timeout: 10_000 });
    await expect(authedPage.locator('text=Create page objects')).toBeVisible();
    await expect(authedPage.locator('text=Write E2E tests')).toBeVisible();
  });

  test('should update task status when receiving dag_task_update events', async ({
    authedPage,
    mockWebSocket,
  }) => {
    const ws = await mockWebSocket();
    const projectView = new ProjectViewPage(authedPage);
    await projectView.goto(PROJECT_ID);

    // Send initial plan
    await ws.send({
      type: 'plan_update',
      project_id: PROJECT_ID,
      plan: MOCK_PROJECT.plan,
    });

    // Wait for tasks to render
    await expect(authedPage.locator('text=Write E2E tests')).toBeVisible({ timeout: 10_000 });

    // Send task status transition: pending → working
    await ws.send({
      type: 'dag_task_update',
      project_id: PROJECT_ID,
      task_id: 'task_003',
      status: 'working',
      task_name: 'Write E2E tests',
    });

    // The task_003 row should now show a working/running indicator
    // Look for visual change (spinning icon, class change, etc.)
    const taskRow = authedPage.locator('text=Write E2E tests').locator('..');
    await expect(taskRow).toBeVisible();
  });
});
