/**
 * Smoke test to verify Playwright infrastructure is wired correctly.
 * This test only checks that the config, fixtures, and page objects load without errors.
 * Real E2E scenarios live in separate test files (task_004).
 */
import { test, expect } from './fixtures/base';
import { DashboardPage } from './page-objects/DashboardPage';
import { ProjectViewPage } from './page-objects/ProjectViewPage';
import { PlanViewPage } from './page-objects/PlanViewPage';

test.describe('Infrastructure Smoke Test', () => {
  test('fixtures and page objects initialize without error', async ({ authedPage }) => {
    // Verify fixtures work — authedPage is a Page with auth injected
    expect(authedPage).toBeTruthy();

    // Verify page objects instantiate cleanly
    const dashboard = new DashboardPage(authedPage);
    const projectView = new ProjectViewPage(authedPage);
    const planView = new PlanViewPage(authedPage);

    expect(dashboard.projectCards).toBeTruthy();
    expect(projectView.projectTitle).toBeTruthy();
    expect(planView.taskSteps).toBeTruthy();
  });
});
