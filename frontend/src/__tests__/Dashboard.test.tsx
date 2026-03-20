/**
 * Dashboard.test.tsx — Tests for running card glow class and agent icon CSS classes.
 *
 * Verifies:
 * - Running projects get the `card-running` CSS class on their card element
 * - Non-running projects do NOT get `card-running`
 * - Active agents get `agent-icon-active` class
 * - Idle agents get `agent-icon-idle` class
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../pages/Dashboard';
import type { Project, WSEvent } from '../types';

// ── Mocks ──────────────────────────────────────────────────────────

// Mock api module
const mockGetProjects = vi.fn<() => Promise<Project[]>>();
const mockGetTasks = vi.fn().mockResolvedValue([]);
vi.mock('../api', () => ({
  getProjects: (...args: unknown[]) => mockGetProjects(...(args as [])),
  getTasks: (...args: unknown[]) => mockGetTasks(...args),
  deleteProject: vi.fn(),
  updateProject: vi.fn(),
}));

// Mock WebSocket context — capture the event handler so we can push events
let wsHandler: ((event: WSEvent) => void) | null = null;
vi.mock('../WebSocketContext', () => ({
  useWSSubscribe: (handler: (event: WSEvent) => void) => {
    wsHandler = handler;
    return { connected: true };
  },
}));

// Mock ThemeContext
vi.mock('../ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

// Mock child components to avoid deep dependency trees
vi.mock('../components/Skeleton', () => ({
  DashboardSkeleton: () => <div data-testid="skeleton">Loading...</div>,
}));
vi.mock('../components/ErrorState', () => ({
  default: () => <div data-testid="error-state">Error</div>,
}));
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock('../components/AgentLogPanel', () => ({
  default: () => <div data-testid="agent-log">Log</div>,
}));
vi.mock('../components/WelcomeHero', () => ({
  default: () => <div data-testid="welcome-hero">Welcome</div>,
}));
vi.mock('../hooks/usePageTitle', () => ({
  usePageTitle: vi.fn(),
}));

// Mock constants to return predictable values
vi.mock('../constants', () => ({
  AGENT_ICONS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) { return p === 'developer' ? '💻' : '🤖'; },
    has() { return true; },
  }),
  AGENT_LABELS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) { return p; },
  }),
  getAgentAccent: () => ({ color: '#638cff', glow: 'rgba(99,140,255,0.15)', bg: 'rgba(99,140,255,0.06)' }),
}));

// ── Helpers ──────────────────────────────────────────────────────────

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    project_id: 'proj-1',
    project_name: 'Test Project',
    project_dir: '/tmp/test',
    status: 'idle',
    is_running: false,
    is_paused: false,
    turn_count: 5,
    total_cost_usd: 0,
    agents: ['orchestrator', 'developer', 'reviewer'],
    multi_agent: true,
    last_message: null,
    ...overrides,
  };
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

// ── Tests ──────────────────────────────────────────────────────────

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsHandler = null;
  });

  describe('card-running class', () => {
    it('test_card_class_when_project_running_should_have_card_running', async () => {
      const runningProject = makeProject({ status: 'running', is_running: true });
      mockGetProjects.mockResolvedValue([runningProject]);

      renderDashboard();

      const card = await screen.findByRole('button', { name: /Open project Test Project/i });
      expect(card).toHaveClass('card-running');
    });

    it('test_card_class_when_project_idle_should_not_have_card_running', async () => {
      const idleProject = makeProject({ status: 'idle' });
      mockGetProjects.mockResolvedValue([idleProject]);

      renderDashboard();

      const card = await screen.findByRole('button', { name: /Open project Test Project/i });
      expect(card).not.toHaveClass('card-running');
    });

    it('test_card_class_when_project_paused_should_not_have_card_running', async () => {
      const pausedProject = makeProject({ status: 'paused', is_paused: true });
      mockGetProjects.mockResolvedValue([pausedProject]);

      renderDashboard();

      const card = await screen.findByRole('button', { name: /Open project Test Project/i });
      expect(card).not.toHaveClass('card-running');
    });
  });

  describe('agent icon CSS classes', () => {
    it('test_agent_icon_when_agent_active_should_have_agent_icon_active_class', async () => {
      const project = makeProject({ status: 'running', is_running: true });
      mockGetProjects.mockResolvedValue([project]);

      renderDashboard();

      // Wait for project card to render
      await screen.findByRole('button', { name: /Open project Test Project/i });

      // Simulate an agent_started WS event to mark 'developer' as active
      expect(wsHandler).not.toBeNull();
      wsHandler!({
        type: 'agent_started',
        project_id: 'proj-1',
        agent: 'developer',
        task: 'Building feature',
        timestamp: Date.now() / 1000,
      });

      // Find the developer agent icon by its title
      const icon = await screen.findByTitle('developer (working)');
      expect(icon).toHaveClass('agent-icon-active');
      expect(icon).not.toHaveClass('agent-icon-idle');
    });

    it('test_agent_icon_when_agent_idle_should_have_agent_icon_idle_class', async () => {
      const project = makeProject({ status: 'idle' });
      mockGetProjects.mockResolvedValue([project]);

      renderDashboard();

      await screen.findByRole('button', { name: /Open project Test Project/i });

      // Without any WS events, all agents should be idle
      const icons = screen.getAllByTitle(/^(developer|reviewer)$/);
      for (const icon of icons) {
        expect(icon).toHaveClass('agent-icon-idle');
        expect(icon).not.toHaveClass('agent-icon-active');
      }
    });
  });
});
