/**
 * PlanView.test.tsx — Tests for Vision glass-panel styling and doneFlash on task completion.
 *
 * Verifies:
 * - Vision section has `glass-panel` and `glow-border-blue` CSS classes
 * - Vision section renders icon, label, and vision text
 * - Task rows get `agent-done-flash` class when they just completed
 * - Task rows do NOT get `agent-done-flash` when they were already done
 * - The DONE badge with plan-complete-badge class appears on just-completed tasks
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import PlanView from '../components/PlanView';
import type { DagGraph } from '../components/planViewHelpers';

// Mock constants
vi.mock('../constants', () => ({
  AGENT_ICONS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) {
      const map: Record<string, string> = {
        frontend_developer: '🎨', backend_developer: '⚡', test_engineer: '🧪',
      };
      return map[p] ?? '🔧';
    },
    has() { return true; },
  }),
  AGENT_LABELS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) {
      const map: Record<string, string> = {
        frontend_developer: 'Frontend', backend_developer: 'Backend', test_engineer: 'Tester',
      };
      return map[p] ?? p;
    },
    has() { return true; },
  }),
  getAgentAccent: () => ({ color: '#638cff', glow: 'rgba(99,140,255,0.15)', bg: 'rgba(99,140,255,0.06)' }),
}));

// Mock useFeedback
vi.mock('../hooks/useFeedback', () => ({
  useFeedback: () => ({
    onTaskComplete: vi.fn(),
    onTaskFailed: vi.fn(),
    onAllComplete: vi.fn(),
    onTaskStarted: vi.fn(),
  }),
}));

// Mock CSS imports
vi.mock('../components/PlanView.css', () => ({}));
vi.mock('../styles/animations.css', () => ({}));

// ── Helpers ──────────────────────────────────────────────────────────

function makeDagGraph(vision?: string): DagGraph {
  return {
    vision: vision ?? 'Build a full-stack authentication system with JWT tokens',
    tasks: [
      { id: 'task_001', role: 'frontend_developer', goal: 'Create login page', depends_on: [] },
      { id: 'task_002', role: 'backend_developer', goal: 'Implement JWT auth API', depends_on: [] },
      { id: 'task_003', role: 'test_engineer', goal: 'Write integration tests', depends_on: ['task_001', 'task_002'] },
    ],
  };
}

// ── Tests ──────────────────────────────────────────────────────────

describe('PlanView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Vision glass-panel', () => {
    it('test_vision_panel_when_dag_has_vision_should_have_glass_panel_class', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      // Find the vision container
      const glassPanel = container.querySelector('.glass-panel');
      expect(glassPanel).not.toBeNull();
      expect(glassPanel).toHaveClass('glow-border-blue');
    });

    it('test_vision_panel_when_dag_has_vision_should_render_vision_text', () => {
      render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph('Build amazing authentication')}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      expect(screen.getByText('Build amazing authentication')).toBeInTheDocument();
    });

    it('test_vision_panel_when_dag_has_vision_should_render_vision_label', () => {
      render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      expect(screen.getByText('Vision')).toBeInTheDocument();
    });

    it('test_vision_panel_when_no_vision_should_not_render_glass_panel', () => {
      const graphWithoutVision: DagGraph = {
        tasks: [
          { id: 'task_001', role: 'frontend_developer', goal: 'Create login page', depends_on: [] },
        ],
      };

      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={graphWithoutVision}
          dagTaskStatus={{ task_001: 'pending' }}
        />,
      );

      const glassPanel = container.querySelector('.glass-panel');
      expect(glassPanel).toBeNull();
    });
  });

  describe('doneFlash on task completion', () => {
    it('test_task_row_when_just_completed_should_have_agent_done_flash_class', () => {
      // First render: task is in_progress
      const { container, rerender } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'working', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      // Second render: task transitions to completed
      rerender(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'completed', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      // Find task_001's row — it should have agent-done-flash
      const taskRows = container.querySelectorAll('[role="listitem"]');
      const task1Row = taskRows[0]; // First task in the list
      expect(task1Row).toHaveClass('agent-done-flash');
      expect(task1Row).toHaveClass('plan-status-slide-in');
    });

    it('test_task_row_when_already_done_without_transition_should_not_have_done_flash', () => {
      // Render with task already completed (no transition)
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'completed', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      const taskRows = container.querySelectorAll('[role="listitem"]');
      const task1Row = taskRows[0];
      // No transition happened, so agent-done-flash should NOT be present
      expect(task1Row).not.toHaveClass('agent-done-flash');
    });

    it('test_done_badge_when_just_completed_should_have_plan_complete_badge_class', () => {
      // First render: task is working
      const { container, rerender } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'working', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      // Transition to completed
      rerender(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'completed', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      // The "✓ DONE" badge should use plan-complete-badge class
      const doneBadge = container.querySelector('.plan-complete-badge');
      expect(doneBadge).not.toBeNull();
      expect(doneBadge).toHaveTextContent('✓ DONE');
    });
  });
});
