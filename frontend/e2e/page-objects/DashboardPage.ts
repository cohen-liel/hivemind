import type { Page, Locator } from '@playwright/test';

/**
 * Page Object Model for the Dashboard (route: /).
 * Encapsulates locators and actions for the main projects list view.
 */
export class DashboardPage {
  readonly page: Page;

  // ── Layout ──
  readonly heading: Locator;
  readonly searchInput: Locator;
  readonly filterGroup: Locator;
  readonly newProjectButton: Locator;

  // ── Project cards ──
  readonly projectCards: Locator;

  // ── Welcome / empty state ──
  readonly welcomeHero: Locator;

  // ── Theme toggle ──
  readonly themeToggle: Locator;

  constructor(page: Page) {
    this.page = page;

    this.heading = page.locator('h1, h2').filter({ hasText: /projects|dashboard/i }).first();
    this.searchInput = page.getByLabel('Search projects');
    this.filterGroup = page.getByRole('group', { name: /filter projects/i });
    this.newProjectButton = page.getByRole('button', { name: /new project/i });
    this.projectCards = page.locator('[role="button"][aria-label*="Open project"]');
    this.welcomeHero = page.getByRole('region', { name: /welcome/i });
    this.themeToggle = page.getByLabel(/switch to (light|dark) mode/i);
  }

  /** Navigate to the dashboard. */
  async goto(): Promise<void> {
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  /** Get the number of visible project cards. */
  async getProjectCount(): Promise<number> {
    return this.projectCards.count();
  }

  /** Click a project card by project name. */
  async openProject(name: string): Promise<void> {
    await this.projectCards.filter({ hasText: name }).first().click();
  }

  /** Type into the search input. */
  async search(query: string): Promise<void> {
    await this.searchInput.fill(query);
  }

  /** Click a status filter chip (e.g. "all", "running", "idle"). */
  async filterByStatus(status: string): Promise<void> {
    await this.filterGroup
      .getByRole('button', { name: new RegExp(status, 'i') })
      .click();
  }

  /** Get a single project card locator by name. */
  getProjectCard(name: string): Locator {
    return this.projectCards.filter({ hasText: name }).first();
  }

  /** Check if the welcome/empty state is visible. */
  async isWelcomeVisible(): Promise<boolean> {
    return this.welcomeHero.isVisible();
  }

  /** Click the new project button. */
  async clickNewProject(): Promise<void> {
    await this.newProjectButton.click();
  }
}
