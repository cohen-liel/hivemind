/**
 * projectReducer.test.ts — Tests for WS_TASK_PROGRESS and WS_DAG_PROGRESS
 * action cases in the projectReducer.
 *
 * Covers:
 * - Happy-path task status transitions via WS_TASK_PROGRESS
 * - Progress percentage propagation via WS_DAG_PROGRESS
 * - Edge cases: unknown task IDs, out-of-order events, missing fields
 * - Rapid successive updates (state consistency)
 * - Sequence ID tracking
 */

import { describe, it, expect } from 'vitest';
import {
  projectReducer,
  initialProjectState,
  type ProjectState,
  type ProjectAction,
} from '../projectReducer';
import type { WSEvent } from '../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Create a minimal WSEvent for task_progress */
function makeTaskProgressEvent(overrides: Partial<WSEvent> = {}): WSEvent {
  return {
    type: 'task_progress',
    project_id: 'proj_1',
    timestamp: Date.now() / 1000,
    task_id: 'task_001',
    step: 'working',
    ...overrides,
  };
}

/** Create a minimal WSEvent for dag_progress */
function makeDagProgressEvent(overrides: Partial<WSEvent> = {}): WSEvent {
  return {
    type: 'dag_progress',
    project_id: 'proj_1',
    timestamp: Date.now() / 1000,
    total: 5,
    completed: 2,
    failed: 0,
    running: 1,
    percent: 40,
    ...overrides,
  };
}

/** Create a base state with a project set (needed for WS_DAG_PROGRESS) */
function stateWithProject(overrides: Partial<ProjectState> = {}): ProjectState {
  return {
    ...initialProjectState,
    project: {
      project_id: 'proj_1',
      project_name: 'Test Project',
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
    ...overrides,
  };
}

// ===========================================================================
// WS_TASK_PROGRESS
// ===========================================================================

describe('WS_TASK_PROGRESS', () => {
  // ── Happy paths ──

  it('test_dagTaskStatus_when_step_is_working_should_set_working', () => {
    const event = makeTaskProgressEvent({ task_id: 'task_001', step: 'planning' });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    expect(next.dagTaskStatus['task_001']).toBe('working');
  });

  it('test_dagTaskStatus_when_step_is_complete_should_set_completed', () => {
    const state: ProjectState = {
      ...initialProjectState,
      dagTaskStatus: { task_001: 'working' },
    };
    const event = makeTaskProgressEvent({ task_id: 'task_001', step: 'complete' });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.dagTaskStatus['task_001']).toBe('completed');
  });

  it('test_dagTaskStatus_when_step_is_failed_should_set_failed', () => {
    const state: ProjectState = {
      ...initialProjectState,
      dagTaskStatus: { task_001: 'working' },
    };
    const event = makeTaskProgressEvent({
      task_id: 'task_001',
      step: 'failed',
      failure_reason: 'Syntax error in generated code',
    });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.dagTaskStatus['task_001']).toBe('failed');
    expect(next.dagTaskFailureReasons['task_001']).toBe('Syntax error in generated code');
  });

  it('test_failureReasons_when_step_is_failed_without_reason_should_not_add_entry', () => {
    const event = makeTaskProgressEvent({
      task_id: 'task_002',
      step: 'failed',
      // no failure_reason
    });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    expect(next.dagTaskStatus['task_002']).toBe('failed');
    expect(next.dagTaskFailureReasons['task_002']).toBeUndefined();
  });

  // ── Live agent stream & ticker ──

  it('test_liveAgentStream_when_agent_and_step_description_present_should_update', () => {
    const event = makeTaskProgressEvent({
      task_id: 'task_003',
      agent: 'frontend_developer',
      step: 'implementing',
      step_description: 'Writing React components for dashboard',
    });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    expect(next.liveAgentStream['frontend_developer']).toBeDefined();
    expect(next.liveAgentStream['frontend_developer'].text).toBe(
      'Writing React components for dashboard',
    );
    expect(next.lastTicker).toBe('frontend_developer: Writing React components for dashboard');
  });

  it('test_ticker_when_no_agent_should_use_taskId_as_fallback', () => {
    const event = makeTaskProgressEvent({
      task_id: 'task_004',
      step: 'reviewing',
      // no agent field
    });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    // ticker fallback: taskId used as agent label
    expect(next.lastTicker).toBe('task_004: reviewing');
  });

  it('test_liveAgentStream_when_no_step_description_should_not_update_stream', () => {
    const event = makeTaskProgressEvent({
      task_id: 'task_005',
      agent: 'backend_developer',
      step: 'working',
      // no step_description
    });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    // liveAgentStream should NOT have a new entry — step_description is required
    expect(next.liveAgentStream['backend_developer']).toBeUndefined();
  });

  // ── Edge cases ──

  it('test_reducer_when_task_id_missing_should_return_unchanged_state', () => {
    const event = makeTaskProgressEvent({ task_id: undefined, step: 'working' });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    expect(next).toBe(initialProjectState); // reference equality — no change
  });

  it('test_dagTaskStatus_when_unknown_task_id_should_create_new_entry', () => {
    const state: ProjectState = {
      ...initialProjectState,
      dagTaskStatus: { task_001: 'completed' },
    };
    const event = makeTaskProgressEvent({ task_id: 'task_999', step: 'working' });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(state, action);

    // Unknown task ID gets added, existing entries preserved
    expect(next.dagTaskStatus['task_999']).toBe('working');
    expect(next.dagTaskStatus['task_001']).toBe('completed');
  });

  it('test_dagTaskStatus_when_rapid_successive_updates_should_apply_all_in_order', () => {
    let state = initialProjectState;

    // Simulate rapid succession: pending → working → complete
    const steps = ['planning', 'implementing', 'testing', 'complete'] as const;
    for (const step of steps) {
      const event = makeTaskProgressEvent({ task_id: 'task_010', step });
      state = projectReducer(state, { type: 'WS_TASK_PROGRESS', event });
    }

    expect(state.dagTaskStatus['task_010']).toBe('completed');
  });

  it('test_dagTaskStatus_when_out_of_order_events_should_apply_latest', () => {
    // Simulate out-of-order: "complete" arrives, then a stale "working"
    let state = initialProjectState;

    const completeEvent = makeTaskProgressEvent({
      task_id: 'task_020',
      step: 'complete',
      sequence_id: 10,
    });
    state = projectReducer(state, { type: 'WS_TASK_PROGRESS', event: completeEvent });
    expect(state.dagTaskStatus['task_020']).toBe('completed');

    // A late "working" event arrives — reducer applies it (no built-in guard)
    // This documents current behavior: the reducer does NOT reject stale events
    const staleEvent = makeTaskProgressEvent({
      task_id: 'task_020',
      step: 'working',
      sequence_id: 5,
    });
    state = projectReducer(state, { type: 'WS_TASK_PROGRESS', event: staleEvent });

    // NOTE: The reducer applies events unconditionally for dagTaskStatus updates.
    // This test documents the behavior. If out-of-order protection is added later,
    // update this assertion to expect 'completed'.
    expect(state.dagTaskStatus['task_020']).toBe('working');
  });

  it('test_sequence_id_when_present_should_be_tracked', () => {
    const event = makeTaskProgressEvent({ task_id: 'task_030', step: 'working', sequence_id: 42 });
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    expect(next.lastSequenceId).toBe(42);
  });

  it('test_sequence_id_when_absent_should_preserve_existing', () => {
    const state: ProjectState = { ...initialProjectState, lastSequenceId: 100 };
    const event = makeTaskProgressEvent({ task_id: 'task_031', step: 'working' });
    // no sequence_id on event
    const action: ProjectAction = { type: 'WS_TASK_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.lastSequenceId).toBe(100);
  });

  it('test_multiple_tasks_when_concurrent_updates_should_track_independently', () => {
    let state = initialProjectState;

    // Two tasks progressing concurrently
    state = projectReducer(state, {
      type: 'WS_TASK_PROGRESS',
      event: makeTaskProgressEvent({ task_id: 'task_A', step: 'implementing' }),
    });
    state = projectReducer(state, {
      type: 'WS_TASK_PROGRESS',
      event: makeTaskProgressEvent({ task_id: 'task_B', step: 'testing' }),
    });
    state = projectReducer(state, {
      type: 'WS_TASK_PROGRESS',
      event: makeTaskProgressEvent({ task_id: 'task_A', step: 'complete' }),
    });

    expect(state.dagTaskStatus['task_A']).toBe('completed');
    expect(state.dagTaskStatus['task_B']).toBe('working');
  });
});

// ===========================================================================
// WS_DAG_PROGRESS
// ===========================================================================

describe('WS_DAG_PROGRESS', () => {
  // ── Happy paths ──

  it('test_dagProgress_when_project_exists_should_update_project', () => {
    const state = stateWithProject();
    const event = makeDagProgressEvent({
      total: 10,
      completed: 3,
      failed: 1,
      running: 2,
      percent: 40,
    });
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.project).not.toBeNull();
    expect(next.project!.dag_progress).toEqual({
      total: 10,
      completed: 3,
      failed: 1,
      running: 2,
      percent: 40,
    });
  });

  it('test_dagProgress_when_project_null_should_keep_null', () => {
    const event = makeDagProgressEvent();
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(initialProjectState, action);

    // project should remain null — dag_progress cannot be set without a project
    expect(next.project).toBeNull();
  });

  it('test_dagProgress_when_percent_100_should_reflect_completion', () => {
    const state = stateWithProject();
    const event = makeDagProgressEvent({
      total: 5,
      completed: 5,
      failed: 0,
      running: 0,
      percent: 100,
    });
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.project!.dag_progress!.percent).toBe(100);
    expect(next.project!.dag_progress!.completed).toBe(5);
    expect(next.project!.dag_progress!.running).toBe(0);
  });

  // ── Missing/zero fields ──

  it('test_dagProgress_when_fields_missing_should_default_to_zero', () => {
    const state = stateWithProject();
    // Event with no progress fields at all
    const event: WSEvent = {
      type: 'dag_progress',
      project_id: 'proj_1',
      timestamp: Date.now() / 1000,
      // no total, completed, failed, running, percent
    };
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.project!.dag_progress).toEqual({
      total: 0,
      completed: 0,
      failed: 0,
      running: 0,
      percent: 0,
    });
  });

  // ── Successive updates ──

  it('test_dagProgress_when_successive_updates_should_overwrite_previous', () => {
    let state = stateWithProject();

    // First update
    state = projectReducer(state, {
      type: 'WS_DAG_PROGRESS',
      event: makeDagProgressEvent({ total: 8, completed: 2, percent: 25 }),
    });
    expect(state.project!.dag_progress!.percent).toBe(25);

    // Second update — overwrites
    state = projectReducer(state, {
      type: 'WS_DAG_PROGRESS',
      event: makeDagProgressEvent({ total: 8, completed: 6, percent: 75 }),
    });
    expect(state.project!.dag_progress!.percent).toBe(75);
    expect(state.project!.dag_progress!.completed).toBe(6);
  });

  it('test_dagProgress_when_sequence_id_present_should_be_tracked', () => {
    const state = stateWithProject();
    const event = makeDagProgressEvent({ sequence_id: 55 });
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.lastSequenceId).toBe(55);
  });

  it('test_dagProgress_should_preserve_other_project_fields', () => {
    const state = stateWithProject();
    const event = makeDagProgressEvent({ total: 3, completed: 1, percent: 33 });
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    // Existing project fields must be preserved
    expect(next.project!.project_id).toBe('proj_1');
    expect(next.project!.project_name).toBe('Test Project');
    expect(next.project!.status).toBe('running');
    expect(next.project!.agents).toEqual(['backend_developer']);
  });

  it('test_dagProgress_when_includes_failures_should_reflect_in_state', () => {
    const state = stateWithProject();
    const event = makeDagProgressEvent({
      total: 6,
      completed: 3,
      failed: 2,
      running: 1,
      percent: 50,
    });
    const action: ProjectAction = { type: 'WS_DAG_PROGRESS', event };

    const next = projectReducer(state, action);

    expect(next.project!.dag_progress!.failed).toBe(2);
    expect(next.project!.dag_progress!.running).toBe(1);
  });
});

// ===========================================================================
// Cross-cutting: Both action types together
// ===========================================================================

describe('WS_TASK_PROGRESS + WS_DAG_PROGRESS integration', () => {
  it('test_both_actions_when_interleaved_should_maintain_independent_state', () => {
    let state = stateWithProject();

    // Task progress updates dagTaskStatus
    state = projectReducer(state, {
      type: 'WS_TASK_PROGRESS',
      event: makeTaskProgressEvent({ task_id: 'task_001', step: 'implementing' }),
    });

    // DAG progress updates project.dag_progress
    state = projectReducer(state, {
      type: 'WS_DAG_PROGRESS',
      event: makeDagProgressEvent({ total: 4, completed: 1, running: 1, percent: 25 }),
    });

    // Task completes
    state = projectReducer(state, {
      type: 'WS_TASK_PROGRESS',
      event: makeTaskProgressEvent({ task_id: 'task_001', step: 'complete' }),
    });

    // DAG updates again
    state = projectReducer(state, {
      type: 'WS_DAG_PROGRESS',
      event: makeDagProgressEvent({ total: 4, completed: 2, running: 0, percent: 50 }),
    });

    // Both subsystems should reflect their latest state
    expect(state.dagTaskStatus['task_001']).toBe('completed');
    expect(state.project!.dag_progress!.percent).toBe(50);
    expect(state.project!.dag_progress!.completed).toBe(2);
  });
});
