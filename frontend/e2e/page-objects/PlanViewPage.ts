import type { Page, Locator } from '@playwright/test';

/**
 * Page Object Model for the PlanView component.
 * PlanView is rendered within ProjectView when the "Plan" tab is active.
 * It displays the DAG task graph with vision statement, task steps, and progress.
 */
export class PlanViewPage {
  readonly page: Page;

  // ── Vision ──
  readonly visionPanel: Locator;
  readonly visionText: Locator;

  // ── Task steps ──
  readonly taskSteps: Locator;
  readonly completedSteps: Locator;
  readonly runningSteps: Locator;
  readonly failedSteps: Locator;
  readonly pendingSteps: Locator;

  // ── Progress ──
  readonly progressBar: Locator;
  readonly progressText: Locator;

  // ── Task editing (action buttons) ──
  readonly editButtons: Locator;
  readonly deleteButtons: Locator;
  readonly addTaskButton: Locator;

  // ── Round grouping ──
  readonly roundHeaders: Locator;

  constructor(page: Page) {
    this.page = page;

    // Vision section
    this.visionPanel = page.locator('[class*="glass-panel"], [class*="vision"]');
    this.visionText = this.visionPanel.locator('p, [class*="vision-text"]').first();

    // Task steps — use status icon aria-labels and CSS classes to distinguish states
    this.taskSteps = page.locator('[class*="plan-step"], [class*="task-row"]');
    this.completedSteps = page.locator('[aria-label="Completed"]').locator('..');
    this.runningSteps = page.locator('[class*="step-working"], [class*="running"]');
    this.failedSteps = page.locator('[class*="step-failed"], [class*="failed"]');
    this.pendingSteps = page.locator('[class*="step-pending"], [class*="pending"]');

    // Progress
    this.progressBar = page.locator('[role="progressbar"], [class*="progress-bar"]');
    this.progressText = page.locator('[class*="progress-text"], [class*="progress-label"]');

    // Task editing
    this.editButtons = page.locator('[aria-label^="Edit task"]');
    this.deleteButtons = page.locator('[aria-label^="Delete task"]');
    this.addTaskButton = page.getByRole('button', { name: /add task/i });

    // Rounds
    this.roundHeaders = page.locator('[class*="round-header"], [class*="round-label"]');
  }

  /** Get the vision statement text. */
  async getVisionText(): Promise<string> {
    return (await this.visionText.textContent()) ?? '';
  }

  /** Get the total number of task steps. */
  async getStepCount(): Promise<number> {
    return this.taskSteps.count();
  }

  /** Get counts of steps by status. */
  async getStepCounts(): Promise<{
    completed: number;
    running: number;
    failed: number;
    pending: number;
    total: number;
  }> {
    const [completed, running, failed, pending] = await Promise.all([
      this.completedSteps.count(),
      this.runningSteps.count(),
      this.failedSteps.count(),
      this.pendingSteps.count(),
    ]);
    return {
      completed,
      running,
      failed,
      pending,
      total: completed + running + failed + pending,
    };
  }

  /** Get a specific task step by its task ID text (e.g. "task_001"). */
  getStep(taskId: string): Locator {
    return this.taskSteps.filter({ hasText: taskId }).first();
  }

  /** Click the edit button for a specific task. */
  async editTask(taskId: string): Promise<void> {
    await this.page.locator(`[aria-label="Edit task ${taskId}"]`).click();
  }

  /** Click the delete button for a specific task. */
  async deleteTask(taskId: string): Promise<void> {
    await this.page.locator(`[aria-label="Delete task ${taskId}"]`).click();
  }

  /** Check if the progress bar is visible. */
  async isProgressVisible(): Promise<boolean> {
    return this.progressBar.isVisible();
  }

  /** Get the number of round grouping headers. */
  async getRoundCount(): Promise<number> {
    return this.roundHeaders.count();
  }
}
