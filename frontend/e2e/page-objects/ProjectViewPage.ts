import type { Page, Locator } from '@playwright/test';

/**
 * Page Object Model for the Project Detail View (route: /project/:id).
 * Covers the orchestration page with activity feed, agent states, DAG view, etc.
 */
export class ProjectViewPage {
  readonly page: Page;

  // ── Header ──
  readonly projectTitle: Locator;
  readonly statusBadge: Locator;

  // ── Tab navigation (desktop) ──
  readonly desktopTabs: Locator;

  // ── Agent states panel ──
  readonly agentCards: Locator;

  // ── Activity feed ──
  readonly activityFeed: Locator;
  readonly activityEntries: Locator;

  // ── Message input ──
  readonly messageInput: Locator;
  readonly sendButton: Locator;

  // ── DAG visualization ──
  readonly dagContainer: Locator;

  // ── Approval modal ──
  readonly approvalModal: Locator;
  readonly approveButton: Locator;
  readonly rejectButton: Locator;

  // ── Loading / Error states ──
  readonly loadingSkeleton: Locator;
  readonly errorState: Locator;

  constructor(page: Page) {
    this.page = page;

    // Header
    this.projectTitle = page.locator('h1, h2').first();
    this.statusBadge = page.locator('[role="status"]').first();

    // Tabs
    this.desktopTabs = page.locator('[role="tablist"], .desktop-tabs');

    // Agents
    this.agentCards = page.locator('[class*="agent-card"], [data-agent]');

    // Activity
    this.activityFeed = page.locator('[role="log"], [class*="activity"]');
    this.activityEntries = this.activityFeed.locator('[class*="entry"], li');

    // Message input
    this.messageInput = page.locator('textarea, input[type="text"]').last();
    this.sendButton = page.getByRole('button', { name: /send/i });

    // DAG
    this.dagContainer = page.locator('[class*="dag"], [class*="plan"]');

    // Approval
    this.approvalModal = page.locator('[role="dialog"]');
    this.approveButton = page.getByRole('button', { name: /approve/i });
    this.rejectButton = page.getByRole('button', { name: /reject|deny/i });

    // States
    this.loadingSkeleton = page.locator('[class*="skeleton"]');
    this.errorState = page.locator('[class*="error-state"]');
  }

  /** Navigate to a specific project by ID. */
  async goto(projectId: string): Promise<void> {
    await this.page.goto(`/project/${projectId}`);
    await this.page.waitForLoadState('networkidle');
  }

  /** Wait for the project to finish loading (skeleton disappears). */
  async waitForLoaded(): Promise<void> {
    await this.loadingSkeleton.waitFor({ state: 'hidden', timeout: 10_000 });
  }

  /** Get the project title text. */
  async getTitle(): Promise<string> {
    return (await this.projectTitle.textContent()) ?? '';
  }

  /** Switch to a desktop tab by name (e.g. "Plan", "Files", "Chat"). */
  async switchTab(tabName: string): Promise<void> {
    await this.desktopTabs
      .getByRole('tab', { name: new RegExp(tabName, 'i') })
      .or(this.desktopTabs.locator('button').filter({ hasText: new RegExp(tabName, 'i') }))
      .first()
      .click();
  }

  /** Send a message in the chat input. */
  async sendMessage(text: string): Promise<void> {
    await this.messageInput.fill(text);
    await this.sendButton.click();
  }

  /** Get the count of visible activity entries. */
  async getActivityCount(): Promise<number> {
    return this.activityEntries.count();
  }

  /** Check if the approval modal is visible. */
  async isApprovalVisible(): Promise<boolean> {
    return this.approvalModal.isVisible();
  }

  /** Click approve in the approval modal. */
  async approve(): Promise<void> {
    await this.approveButton.click();
  }

  /** Get agent card locator by agent name/role. */
  getAgentCard(name: string): Locator {
    return this.agentCards.filter({ hasText: new RegExp(name, 'i') }).first();
  }

  /** Check if any agent card shows a "working" state. */
  async hasWorkingAgent(): Promise<boolean> {
    const workingCards = this.agentCards.filter({ hasText: /working|running/i });
    return (await workingCards.count()) > 0;
  }
}
