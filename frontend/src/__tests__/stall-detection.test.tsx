/**
 * stall-detection.test.tsx — Tests for phase-aware stall detection on Dashboard.
 *
 * Verifies:
 * - No false stall warnings during recognized orchestrator startup phases
 * - Stall warnings correctly suppressed when seconds_since_progress ≤ 300 during startup
 * - Stall warnings appear when genuinely stalled (>300s during startup, >120s normally)
 * - isOrchestratorInStartupPhase() correctly identifies startup phases
 * - useSmartHeartbeat uses higher thresholds during startup phases
 *
 * Naming: test_<what>_when_<condition>_should_<expected>
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../pages/Dashboard';
import { isOrchestratorInStartupPhase } from '../hooks/useSmartHeartbeat';
import type { Project, WSEvent, AgentState } from '../types';

// ── Mocks ──────────────────────────────────────────────────────────

const mockGetProjects = vi.fn<() => Promise<Project[]>>();
const mockGetTasks = vi.fn().mockResolvedValue([]);
vi.mock('../api', () => ({
  getProjects: (...args: unknown[]) => mockGetProjects(...(args as [])),
  getTasks: (...args: unknown[]) => mockGetTasks(...args),
  deleteProject: vi.fn(),
  updateProject: vi.fn(),
}));

let wsHandler: ((event: WSEvent) => void) | null = null;
vi.mock('../WebSocketContext', () => ({
  useWSSubscribe: (handler: (event: WSEvent) => void) => {
    wsHandler = handler;
    return { connected: true };
  },
}));

vi.mock('../ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

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
    project_id: 'proj-stall-test',
    project_name: 'Stall Test Project',
    project_dir: '/tmp/test',
    status: 'idle',
    is_running: false,
    is_paused: false,
    turn_count: 5,
    total_cost_usd: 0,
    agents: ['orchestrator', 'developer'],
    multi_agent: true,
    last_message: null,
    ...overrides,
  };
}

function makeAgentState(overrides: Partial<AgentState> = {}): AgentState {
  return {
    name: 'orchestrator',
    state: 'working',
    cost: 0,
    turns: 0,
    duration: 0,
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

// ── isOrchestratorInStartupPhase() unit tests ────────────────────

describe('isOrchestratorInStartupPhase', () => {
  it('test_startup_phase_when_orchestrator_loading_context_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Loading project context and memory' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_architect_reviewing_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Architect reviewing codebase structure' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_pm_planning_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'PM creating task graph for feature' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_loading_lessons_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Loading lessons learned from previous runs' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_manifest_loading_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ current_tool: 'Reading manifest file' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_file_tree_scanning_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Scanning file tree for project' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_cross_project_memory_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Loading cross-project memory' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_reviewing_plan_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Critic reviewing plan for correctness' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_evaluating_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Evaluating task dependencies' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_normal_task_execution_should_return_false', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Executing DAG task task_003' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_running_tests_should_return_false', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'Running test suite' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_orchestrator_idle_should_return_false', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ state: 'idle', task: 'Loading context' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_no_orchestrator_should_return_false', () => {
    const states: Record<string, AgentState> = {
      developer: makeAgentState({ name: 'developer', task: 'Building feature' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_empty_task_should_return_false', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: undefined, current_tool: undefined }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_orchestrator_done_should_return_false', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ state: 'done', task: 'Loading context' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(false);
  });

  it('test_startup_phase_when_keyword_in_current_tool_should_return_true', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: '', current_tool: 'architect analysis tool' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });

  it('test_startup_phase_when_case_insensitive_should_match', () => {
    const states: Record<string, AgentState> = {
      orchestrator: makeAgentState({ task: 'LOADING PROJECT CONTEXT' }),
    };
    expect(isOrchestratorInStartupPhase(states)).toBe(true);
  });
});


// ── Dashboard stall display logic tests ──────────────────────────

describe('Dashboard stall display during startup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsHandler = null;
  });

  it('test_health_warning_when_startup_phase_within_300s_should_not_show_warning', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      agent_states: {
        orchestrator: {
          state: 'working',
          task: 'Loading project context and memory',
        },
      },
      diagnostics: {
        health_score: 'degraded',
        warnings_count: 0,
        seconds_since_progress: 60,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // During startup with secSince=60 (≤300), no warning should appear
    expect(screen.queryByText(/Agent stalled/)).toBeNull();
    expect(screen.queryByText(/Degraded/)).toBeNull();
  });

  it('test_health_warning_when_startup_phase_at_250s_should_not_show_warning', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      agent_states: {
        orchestrator: {
          state: 'working',
          task: 'Architect reviewing codebase',
        },
      },
      diagnostics: {
        health_score: 'degraded',
        warnings_count: 1,
        seconds_since_progress: 250,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // 250s during startup phase should still be suppressed (≤300 threshold)
    expect(screen.queryByText(/Agent stalled/)).toBeNull();
    expect(screen.queryByText(/Degraded/)).toBeNull();
  });

  it('test_health_warning_when_startup_phase_over_300s_should_show_critical', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      agent_states: {
        orchestrator: {
          state: 'working',
          task: 'PM creating task graph',
        },
      },
      diagnostics: {
        health_score: 'critical',
        warnings_count: 3,
        seconds_since_progress: 350,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // 350s > 300 even during startup — should show stall warning
    const stallText = await screen.findByText(/Agent stalled/);
    expect(stallText).toBeTruthy();
  });

  it('test_health_warning_when_not_startup_phase_degraded_over_120s_should_show', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      agent_states: {
        orchestrator: {
          state: 'working',
          task: 'Executing DAG task_003',
        },
      },
      diagnostics: {
        health_score: 'degraded',
        warnings_count: 1,
        seconds_since_progress: 130,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // 130s during normal execution (not startup phase) — genuinelyStale
    const degradedText = await screen.findByText(/Degraded/);
    expect(degradedText).toBeTruthy();
  });

  it('test_health_warning_when_not_startup_running_under_120s_degraded_should_suppress', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      agent_states: {
        orchestrator: {
          state: 'working',
          task: 'Running agent task_005',
        },
      },
      diagnostics: {
        health_score: 'degraded',
        warnings_count: 0,
        seconds_since_progress: 50,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // Running, degraded, but secSince=50 (<120) and not genuinelyStale — suppressed
    expect(screen.queryByText(/Agent stalled/)).toBeNull();
    expect(screen.queryByText(/Degraded/)).toBeNull();
  });

  it('test_health_warning_when_idle_any_health_issue_should_show', async () => {
    const project = makeProject({
      status: 'idle',
      is_running: false,
      diagnostics: {
        health_score: 'critical',
        warnings_count: 2,
        seconds_since_progress: 40,
        last_stuckness: Date.now() / 1000,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // Idle project with critical health — should show warning
    const stallText = await screen.findByText(/Agent stalled/);
    expect(stallText).toBeTruthy();
  });

  it('test_health_warning_when_healthy_should_never_show', async () => {
    const project = makeProject({
      status: 'running',
      is_running: true,
      diagnostics: {
        health_score: 'healthy',
        warnings_count: 0,
        seconds_since_progress: 5,
        last_stuckness: null,
      },
    });
    mockGetProjects.mockResolvedValue([project]);

    renderDashboard();
    await screen.findByRole('button', { name: /Open project/i });

    // Healthy = no warnings ever
    expect(screen.queryByText(/Agent stalled/)).toBeNull();
    expect(screen.queryByText(/Degraded/)).toBeNull();
  });
});
