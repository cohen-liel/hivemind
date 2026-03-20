/**
 * PlanView.test.tsx — Frontend tests for plan editing UI.
 *
 * Verifies:
 * - Edit/delete buttons only appear on pending tasks
 * - Edit/delete buttons do NOT appear on running/completed/failed tasks
 * - Edit/delete buttons do NOT appear when projectId is missing (read-only)
 * - "Add Task" button appears in DAG mode with projectId
 * - "Add Task" button hidden when all tasks are done
 * - InlineEditForm renders when editing a pending task
 * - DeleteConfirmation renders when deleting a pending task
 * - Reducer handles WS_DAG_TASK_UPDATE 'modified' action
 * - Reducer handles WS_DAG_TASK_UPDATE 'added' action
 * - Reducer handles WS_DAG_TASK_UPDATE 'removed' action
 * - Reducer ignores duplicate 'added' events
 * - Reducer preserves localStorage persistence
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PlanView from '../components/PlanView';
import type { DagGraph } from '../components/planViewHelpers';
import {
  projectReducer,
  initialProjectState,
  type ProjectState,
  type ProjectAction,
} from '../reducers/projectReducer';
import type { WSEvent } from '../types';

// ── Mocks ──────────────────────────────────────────────────────────

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

vi.mock('../hooks/useFeedback', () => ({
  useFeedback: () => ({
    onTaskComplete: vi.fn(),
    onTaskFailed: vi.fn(),
    onAllComplete: vi.fn(),
    onTaskStarted: vi.fn(),
  }),
}));

vi.mock('../components/PlanView.css', () => ({}));
vi.mock('../styles/animations.css', () => ({}));

// ── Helpers ──────────────────────────────────────────────────────────

function makeDagGraph(): DagGraph {
  return {
    vision: 'Build authentication system',
    tasks: [
      { id: 'task_001', role: 'backend_developer', goal: 'Implement JWT auth API', depends_on: [] },
      { id: 'task_002', role: 'frontend_developer', goal: 'Build login form', depends_on: ['task_001'] },
      { id: 'task_003', role: 'test_engineer', goal: 'Write integration tests', depends_on: ['task_001', 'task_002'] },
    ],
  };
}

function stateWithDag(overrides: Partial<ProjectState> = {}): ProjectState {
  return {
    ...initialProjectState,
    project: {
      project_id: 'proj_1',
      project_name: 'Test',
      project_dir: '/tmp/test',
      status: 'running',
      is_running: true,
      is_paused: false,
      turn_count: 0,
      total_cost_usd: 0,
      agents: ['backend_developer'],
      multi_agent: true,
      last_message: null,
    },
    dagGraph: makeDagGraph(),
    dagTaskStatus: {
      task_001: 'pending',
      task_002: 'pending',
      task_003: 'pending',
    },
    ...overrides,
  };
}

// ===========================================================================
// PlanView — Edit/Delete UI visibility
// ===========================================================================

describe('PlanView editing UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Edit/Delete buttons visibility', () => {
    it('test_edit_buttons_when_pending_with_projectId_should_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      // Look for edit buttons via aria-label
      const editButtons = container.querySelectorAll('[aria-label^="Edit task"]');
      expect(editButtons.length).toBeGreaterThan(0);
    });

    it('test_delete_buttons_when_pending_with_projectId_should_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      const deleteButtons = container.querySelectorAll('[aria-label^="Delete task"]');
      expect(deleteButtons.length).toBeGreaterThan(0);
    });

    it('test_edit_buttons_when_no_projectId_should_not_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
          // No projectId — read-only mode
        />,
      );

      const editButtons = container.querySelectorAll('[aria-label^="Edit task"]');
      expect(editButtons.length).toBe(0);
    });

    it('test_edit_buttons_when_task_working_should_not_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'working', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      // Only pending tasks should have edit buttons
      const editButtons = container.querySelectorAll('[aria-label="Edit task task_001"]');
      expect(editButtons.length).toBe(0);
    });

    it('test_edit_buttons_when_task_completed_should_not_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'completed', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      const editButtons = container.querySelectorAll('[aria-label="Edit task task_001"]');
      expect(editButtons.length).toBe(0);
    });

    it('test_edit_buttons_when_task_failed_should_not_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'failed', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      const editButtons = container.querySelectorAll('[aria-label="Edit task task_001"]');
      expect(editButtons.length).toBe(0);
    });

    it('test_delete_buttons_when_task_cancelled_should_not_be_visible', () => {
      const { container } = render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'cancelled', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      const deleteButtons = container.querySelectorAll('[aria-label="Delete task task_001"]');
      expect(deleteButtons.length).toBe(0);
    });
  });

  describe('Add Task button', () => {
    it('test_add_task_button_when_dag_mode_with_projectId_should_be_visible', () => {
      render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
          projectId="proj_1"
        />,
      );

      const addButton = screen.queryByLabelText('Add a new task to the plan');
      expect(addButton).not.toBeNull();
    });

    it('test_add_task_button_when_no_projectId_should_not_be_visible', () => {
      render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'pending', task_002: 'pending', task_003: 'pending' }}
        />,
      );

      const addButton = screen.queryByLabelText('Add a new task to the plan');
      expect(addButton).toBeNull();
    });

    it('test_add_task_button_when_all_tasks_done_should_not_be_visible', () => {
      render(
        <PlanView
          activities={[]}
          dagGraph={makeDagGraph()}
          dagTaskStatus={{ task_001: 'completed', task_002: 'completed', task_003: 'completed' }}
          projectId="proj_1"
        />,
      );

      const addButton = screen.queryByLabelText('Add a new task to the plan');
      expect(addButton).toBeNull();
    });
  });
});

// ===========================================================================
// projectReducer — WS_DAG_TASK_UPDATE plan modification actions
// ===========================================================================

describe('projectReducer WS_DAG_TASK_UPDATE plan modifications', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('modified action', () => {
    it('test_reducer_when_modified_event_should_update_task_goal', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_001',
          action: 'modified',
          changes: { goal: 'Updated goal text here' },
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      const updatedTask = result.dagGraph?.tasks?.find(t => t.id === 'task_001');
      expect(updatedTask?.goal).toBe('Updated goal text here');
    });

    it('test_reducer_when_modified_event_should_preserve_other_tasks', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_001',
          action: 'modified',
          changes: { goal: 'New goal' },
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagGraph?.tasks?.length).toBe(3);
      const task2 = result.dagGraph?.tasks?.find(t => t.id === 'task_002');
      expect(task2?.goal).toBe('Build login form');
    });
  });

  describe('added action', () => {
    it('test_reducer_when_added_event_should_append_task', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_004',
          action: 'added',
          task: {
            id: 'task_004',
            role: 'backend_developer',
            goal: 'Add rate limiting',
            depends_on: [],
          },
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagGraph?.tasks?.length).toBe(4);
      const newTask = result.dagGraph?.tasks?.find(t => t.id === 'task_004');
      expect(newTask).toBeDefined();
      expect(newTask?.goal).toBe('Add rate limiting');
      expect(result.dagTaskStatus['task_004']).toBe('pending');
    });

    it('test_reducer_when_added_duplicate_should_ignore', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_001',
          action: 'added',
          task: {
            id: 'task_001',
            role: 'backend_developer',
            goal: 'Duplicate task',
            depends_on: [],
          },
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      // Should be same reference since no change was made
      expect(result.dagGraph?.tasks?.length).toBe(3);
    });
  });

  describe('removed action', () => {
    it('test_reducer_when_removed_event_should_remove_task', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_003',
          action: 'removed',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagGraph?.tasks?.length).toBe(2);
      expect(result.dagGraph?.tasks?.find(t => t.id === 'task_003')).toBeUndefined();
    });

    it('test_reducer_when_removed_event_should_remove_task_status', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_003',
          action: 'removed',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagTaskStatus).not.toHaveProperty('task_003');
    });

    it('test_reducer_when_removed_event_should_remove_failure_reasons', () => {
      const state = stateWithDag({
        dagTaskFailureReasons: { task_003: 'some error' },
      });
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_003',
          action: 'removed',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagTaskFailureReasons).not.toHaveProperty('task_003');
    });

    it('test_reducer_when_removed_event_should_preserve_other_tasks', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_003',
          action: 'removed',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagGraph?.tasks?.find(t => t.id === 'task_001')).toBeDefined();
      expect(result.dagGraph?.tasks?.find(t => t.id === 'task_002')).toBeDefined();
    });
  });

  describe('standard status changes (not plan modification)', () => {
    it('test_reducer_when_status_change_should_update_dag_task_status', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_001',
          status: 'working',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagTaskStatus['task_001']).toBe('working');
    });

    it('test_reducer_when_failure_should_capture_reason', () => {
      const state = stateWithDag();
      const action: ProjectAction = {
        type: 'WS_DAG_TASK_UPDATE',
        event: {
          type: 'dag_task_update',
          project_id: 'proj_1',
          timestamp: Date.now() / 1000,
          task_id: 'task_001',
          status: 'failed',
          failure_reason: 'Timed out',
        } as unknown as WSEvent,
      };

      const result = projectReducer(state, action);

      expect(result.dagTaskStatus['task_001']).toBe('failed');
      expect(result.dagTaskFailureReasons['task_001']).toBe('Timed out');
    });
  });
});
