/**
 * planPersistence.test.ts — Tests for incremental plan system frontend logic.
 *
 * Covers:
 * - projectReducer WS_TASK_GRAPH merge behavior (no-reset of completed/skipped)
 * - projectReducer WS_PLAN_DELTA handler (append + skip)
 * - planViewHelpers: dagToPlanSteps, computeProgress, groupStepsByRound, inferMessageRounds
 * - Edge cases: skip-with-dependents rendering, inject-after-complete, concurrent deltas
 *
 * Task: task_007
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  dagToPlanSteps,
  computeProgress,
  groupStepsByRound,
  type DagGraph,
  type DagTask,
  type PlanStep,
} from '../components/planViewHelpers';

// ============================================================================
// Helpers
// ============================================================================

function makeDagGraph(tasks: DagTask[], vision = 'Test vision'): DagGraph {
  return { vision, tasks };
}

function makeTask(
  id: string,
  role = 'backend_developer',
  goal = 'Do something',
  depends_on: string[] = [],
  opts: Partial<DagTask> = {},
): DagTask {
  return { id, role, goal, depends_on, ...opts };
}

// ============================================================================
// dagToPlanSteps
// ============================================================================

describe('dagToPlanSteps', () => {
  it('test_dagToPlanSteps_when_all_pending_should_map_to_pending', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
    ]);
    const status: Record<string, any> = {};
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps).toHaveLength(2);
    expect(steps[0].status).toBe('pending');
    expect(steps[1].status).toBe('pending');
  });

  it('test_dagToPlanSteps_when_completed_should_map_to_done', () => {
    const graph = makeDagGraph([makeTask('task_001')]);
    const status = { task_001: 'completed' as const };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[0].status).toBe('done');
  });

  it('test_dagToPlanSteps_when_working_should_map_to_in_progress', () => {
    const graph = makeDagGraph([makeTask('task_001')]);
    const status = { task_001: 'working' as const };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[0].status).toBe('in_progress');
  });

  it('test_dagToPlanSteps_when_failed_should_map_to_error', () => {
    const graph = makeDagGraph([makeTask('task_001')]);
    const status = { task_001: 'failed' as const };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[0].status).toBe('error');
  });

  it('test_dagToPlanSteps_when_skipped_should_map_to_skipped', () => {
    const graph = makeDagGraph([makeTask('task_001')]);
    const status = { task_001: 'skipped' as const };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[0].status).toBe('skipped');
  });

  it('test_dagToPlanSteps_when_cancelled_should_map_to_cancelled', () => {
    const graph = makeDagGraph([makeTask('task_001')]);
    const status = { task_001: 'cancelled' as const };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[0].status).toBe('cancelled');
  });

  it('test_dagToPlanSteps_should_include_task_metadata', () => {
    const graph = makeDagGraph([
      makeTask('task_001', 'frontend_developer', 'Build login page', ['task_000']),
    ]);
    const status = { task_001: 'pending' as const };
    const failureReasons = { task_001: 'Timed out' };
    const steps = dagToPlanSteps(graph, status, failureReasons);
    expect(steps[0].taskId).toBe('task_001');
    expect(steps[0].agent).toBe('frontend_developer');
    expect(steps[0].text).toBe('Build login page');
    expect(steps[0].dependsOn).toEqual(['task_000']);
    expect(steps[0].failureReason).toBe('Timed out');
  });

  it('test_dagToPlanSteps_when_empty_graph_should_return_empty', () => {
    const graph = makeDagGraph([]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps).toHaveLength(0);
  });

  it('test_dagToPlanSteps_when_graph_tasks_undefined_should_return_empty', () => {
    const graph: DagGraph = { vision: 'test' };
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps).toHaveLength(0);
  });

  it('test_dagToPlanSteps_should_assign_1_indexed_positions', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      makeTask('task_003'),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[0].index).toBe(1);
    expect(steps[1].index).toBe(2);
    expect(steps[2].index).toBe(3);
  });

  it('test_dagToPlanSteps_should_mark_remediation_tasks', () => {
    const graph = makeDagGraph([
      makeTask('task_001', 'backend_developer', 'Fix build', [], { is_remediation: true }),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[0].isRemediation).toBe(true);
  });
});

// ============================================================================
// computeProgress
// ============================================================================

describe('computeProgress', () => {
  function makeSteps(statuses: PlanStep['status'][]): PlanStep[] {
    return statuses.map((status, i) => ({
      index: i + 1,
      text: `Task ${i + 1}`,
      status,
      messageRound: 1,
    }));
  }

  it('test_computeProgress_when_all_done_should_report_100_pct', () => {
    const progress = computeProgress(makeSteps(['done', 'done', 'done']));
    expect(progress.total).toBe(3);
    expect(progress.completed).toBe(3);
    expect(progress.actionable).toBe(3);
    expect(progress.isAllDone).toBe(true);
    expect(progress.pct).toBe(100);
  });

  it('test_computeProgress_when_some_skipped_should_exclude_from_actionable', () => {
    const progress = computeProgress(makeSteps(['done', 'skipped', 'done']));
    expect(progress.total).toBe(3);
    expect(progress.skipped).toBe(1);
    expect(progress.actionable).toBe(2);
    expect(progress.completed).toBe(2);
    expect(progress.isAllDone).toBe(true);
    expect(progress.pct).toBe(100);
  });

  it('test_computeProgress_when_all_skipped_should_not_be_all_done', () => {
    // actionable = 0, so isAllDone = false (0 > 0 is false)
    const progress = computeProgress(makeSteps(['skipped', 'skipped']));
    expect(progress.actionable).toBe(0);
    expect(progress.isAllDone).toBe(false);
    expect(progress.pct).toBe(0);
  });

  it('test_computeProgress_when_has_failures_should_flag_them', () => {
    const progress = computeProgress(makeSteps(['done', 'error', 'cancelled']));
    expect(progress.hasFailures).toBe(true);
    expect(progress.failed).toBe(1);
    expect(progress.cancelled).toBe(1);
  });

  it('test_computeProgress_when_in_progress_should_track_count', () => {
    const progress = computeProgress(makeSteps(['done', 'in_progress', 'pending']));
    expect(progress.inProgress).toBe(1);
    expect(progress.isAllDone).toBe(false);
    expect(progress.pct).toBe(33);
  });

  it('test_computeProgress_when_empty_should_return_zeroes', () => {
    const progress = computeProgress([]);
    expect(progress.total).toBe(0);
    expect(progress.actionable).toBe(0);
    expect(progress.isAllDone).toBe(false);
    expect(progress.pct).toBe(0);
  });

  it('test_computeProgress_when_mixed_done_and_skipped_partial_should_not_be_complete', () => {
    const progress = computeProgress(makeSteps(['done', 'skipped', 'pending']));
    expect(progress.actionable).toBe(2);
    expect(progress.completed).toBe(1);
    expect(progress.isAllDone).toBe(false);
    expect(progress.pct).toBe(50);
  });
});

// ============================================================================
// groupStepsByRound
// ============================================================================

describe('groupStepsByRound', () => {
  it('test_groupStepsByRound_when_single_round_should_return_one_group', () => {
    const steps: PlanStep[] = [
      { index: 1, text: 'A', status: 'pending', messageRound: 1 },
      { index: 2, text: 'B', status: 'done', messageRound: 1 },
    ];
    const groups = groupStepsByRound(steps);
    expect(groups).toHaveLength(1);
    expect(groups[0].round).toBe(1);
    expect(groups[0].label).toBe('Initial plan');
    expect(groups[0].steps).toHaveLength(2);
  });

  it('test_groupStepsByRound_when_multiple_rounds_should_create_groups', () => {
    const steps: PlanStep[] = [
      { index: 1, text: 'A', status: 'done', messageRound: 1 },
      { index: 2, text: 'B', status: 'skipped', messageRound: 1 },
      { index: 3, text: 'C', status: 'pending', messageRound: 2 },
      { index: 4, text: 'D', status: 'pending', messageRound: 2 },
    ];
    const groups = groupStepsByRound(steps);
    expect(groups).toHaveLength(2);
    expect(groups[0].label).toBe('Initial plan');
    expect(groups[0].steps).toHaveLength(2);
    expect(groups[1].label).toBe('Added in message #2');
    expect(groups[1].steps).toHaveLength(2);
  });

  it('test_groupStepsByRound_when_empty_should_return_empty', () => {
    const groups = groupStepsByRound([]);
    expect(groups).toHaveLength(0);
  });

  it('test_groupStepsByRound_should_sort_rounds_ascending', () => {
    const steps: PlanStep[] = [
      { index: 1, text: 'Late', status: 'pending', messageRound: 3 },
      { index: 2, text: 'Early', status: 'done', messageRound: 1 },
    ];
    const groups = groupStepsByRound(steps);
    expect(groups[0].round).toBe(1);
    expect(groups[1].round).toBe(3);
  });

  it('test_groupStepsByRound_when_skipped_tasks_in_round_should_be_included', () => {
    const steps: PlanStep[] = [
      { index: 1, text: 'A', status: 'skipped', messageRound: 1 },
      { index: 2, text: 'B', status: 'done', messageRound: 1 },
    ];
    const groups = groupStepsByRound(steps);
    expect(groups[0].steps).toHaveLength(2);
    expect(groups[0].steps[0].status).toBe('skipped');
  });
});

// ============================================================================
// inferMessageRounds (tested indirectly via dagToPlanSteps)
// ============================================================================

describe('inferMessageRounds via dagToPlanSteps', () => {
  it('test_inferMessageRounds_when_consecutive_ids_should_be_round_1', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      makeTask('task_003'),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps.every(s => s.messageRound === 1)).toBe(true);
  });

  it('test_inferMessageRounds_when_id_gap_should_start_new_round', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      // Gap: 002 → 005 means new round
      makeTask('task_005'),
      makeTask('task_006'),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[0].messageRound).toBe(1);
    expect(steps[1].messageRound).toBe(1);
    expect(steps[2].messageRound).toBe(2);
    expect(steps[3].messageRound).toBe(2);
  });

  it('test_inferMessageRounds_when_remediation_task_should_not_start_new_round', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      // Gap but is_remediation → stays in current round
      makeTask('task_005', 'backend_developer', 'Fix build', [], { is_remediation: true }),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[2].messageRound).toBe(1);
  });

  it('test_inferMessageRounds_when_explicit_rounds_should_use_them', () => {
    const graph = makeDagGraph([
      makeTask('task_001', 'backend_developer', 'A', [], { message_round: 1 }),
      makeTask('task_002', 'backend_developer', 'B', [], { message_round: 1 }),
      makeTask('task_003', 'backend_developer', 'C', [], { message_round: 3 }),
    ]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[0].messageRound).toBe(1);
    expect(steps[1].messageRound).toBe(1);
    expect(steps[2].messageRound).toBe(3);
  });
});

// ============================================================================
// Reducer merge behavior (unit-tested via pure logic simulation)
// ============================================================================

describe('projectReducer merge logic (simulated)', () => {
  /*
   * Since we cannot import the reducer directly without heavy React/Context deps,
   * we simulate the merge logic used in WS_TASK_GRAPH and WS_PLAN_DELTA handlers.
   * This tests the ALGORITHM, not the React wiring.
   */

  interface SimulatedState {
    dagGraph: DagGraph | null;
    dagTaskStatus: Record<string, string>;
    dagTaskFailureReasons: Record<string, string>;
  }

  function simulateTaskGraphMerge(
    state: SimulatedState,
    incomingGraph: DagGraph,
    cumulative?: {
      completed_task_ids?: string[];
      failed_task_ids?: string[];
      skipped_task_ids?: string[];
    },
  ): SimulatedState {
    // Replicate the WS_TASK_GRAPH merge logic
    let mergedGraph: DagGraph;
    const mergedStatus = { ...state.dagTaskStatus };

    if (state.dagGraph?.tasks && incomingGraph.tasks) {
      const existingIds = new Set(state.dagGraph.tasks.map(t => t.id));
      const newTasks = incomingGraph.tasks.filter(t => !existingIds.has(t.id));
      mergedGraph = {
        ...incomingGraph,
        tasks: [...state.dagGraph.tasks, ...newTasks],
      };
      for (const t of newTasks) {
        if (!mergedStatus[t.id]) {
          mergedStatus[t.id] = 'pending';
        }
      }
    } else {
      mergedGraph = incomingGraph;
    }

    if (cumulative) {
      for (const id of cumulative.completed_task_ids ?? []) mergedStatus[id] = 'completed';
      for (const id of cumulative.failed_task_ids ?? []) mergedStatus[id] = 'failed';
      for (const id of cumulative.skipped_task_ids ?? []) mergedStatus[id] = 'skipped';
    }

    return { dagGraph: mergedGraph, dagTaskStatus: mergedStatus, dagTaskFailureReasons: state.dagTaskFailureReasons };
  }

  function simulatePlanDelta(
    state: SimulatedState,
    addTasks: DagTask[],
    skipTaskIds: string[],
  ): SimulatedState {
    // Replicate the WS_PLAN_DELTA handler logic
    let mergedGraph = state.dagGraph;
    const mergedStatus = { ...state.dagTaskStatus };

    if (addTasks.length > 0) {
      const existingTasks = mergedGraph?.tasks ?? [];
      const existingIds = new Set(existingTasks.map(t => t.id));
      const newTasks = addTasks.filter(t => !existingIds.has(t.id));
      mergedGraph = { ...mergedGraph, tasks: [...existingTasks, ...newTasks] };
      for (const t of newTasks) {
        mergedStatus[t.id] = 'pending';
      }
    }

    for (const id of skipTaskIds) {
      mergedStatus[id] = 'skipped';
    }

    return { dagGraph: mergedGraph, dagTaskStatus: mergedStatus, dagTaskFailureReasons: state.dagTaskFailureReasons };
  }

  // ── WS_TASK_GRAPH merge tests ──

  describe('WS_TASK_GRAPH merge', () => {
    it('test_task_graph_merge_when_no_existing_graph_should_set_new', () => {
      const state: SimulatedState = { dagGraph: null, dagTaskStatus: {}, dagTaskFailureReasons: {} };
      const incoming = makeDagGraph([makeTask('task_001'), makeTask('task_002')]);
      const result = simulateTaskGraphMerge(state, incoming);
      expect(result.dagGraph?.tasks).toHaveLength(2);
    });

    it('test_task_graph_merge_when_existing_should_preserve_and_append_new', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001')]),
        dagTaskStatus: { task_001: 'completed' },
        dagTaskFailureReasons: {},
      };
      const incoming = makeDagGraph([makeTask('task_001'), makeTask('task_002')]);
      const result = simulateTaskGraphMerge(state, incoming);
      expect(result.dagGraph?.tasks).toHaveLength(2);
      // Existing completed status preserved
      expect(result.dagTaskStatus.task_001).toBe('completed');
      // New task set to pending
      expect(result.dagTaskStatus.task_002).toBe('pending');
    });

    it('test_task_graph_merge_should_not_reset_completed_status', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001'), makeTask('task_002')]),
        dagTaskStatus: { task_001: 'completed', task_002: 'failed' },
        dagTaskFailureReasons: { task_002: 'Build error' },
      };
      const incoming = makeDagGraph([
        makeTask('task_001'),
        makeTask('task_002'),
        makeTask('task_003'),
      ]);
      const result = simulateTaskGraphMerge(state, incoming);
      expect(result.dagTaskStatus.task_001).toBe('completed');
      expect(result.dagTaskStatus.task_002).toBe('failed');
      expect(result.dagTaskStatus.task_003).toBe('pending');
      expect(result.dagTaskFailureReasons.task_002).toBe('Build error');
    });

    it('test_task_graph_merge_when_cumulative_should_restore_statuses', () => {
      const state: SimulatedState = {
        dagGraph: null,
        dagTaskStatus: {},
        dagTaskFailureReasons: {},
      };
      const incoming = makeDagGraph([
        makeTask('task_001'),
        makeTask('task_002'),
        makeTask('task_003'),
      ]);
      const result = simulateTaskGraphMerge(state, incoming, {
        completed_task_ids: ['task_001'],
        failed_task_ids: ['task_002'],
        skipped_task_ids: ['task_003'],
      });
      expect(result.dagTaskStatus.task_001).toBe('completed');
      expect(result.dagTaskStatus.task_002).toBe('failed');
      expect(result.dagTaskStatus.task_003).toBe('skipped');
    });

    it('test_task_graph_merge_when_duplicate_tasks_in_incoming_should_not_duplicate', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001'), makeTask('task_002')]),
        dagTaskStatus: { task_001: 'completed', task_002: 'working' },
        dagTaskFailureReasons: {},
      };
      // All tasks already exist in state
      const incoming = makeDagGraph([makeTask('task_001'), makeTask('task_002')]);
      const result = simulateTaskGraphMerge(state, incoming);
      expect(result.dagGraph?.tasks).toHaveLength(2);
    });
  });

  // ── WS_PLAN_DELTA tests ──

  describe('WS_PLAN_DELTA handling', () => {
    it('test_plan_delta_when_add_tasks_should_append_with_pending_status', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001')]),
        dagTaskStatus: { task_001: 'completed' },
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(
        state,
        [makeTask('task_002'), makeTask('task_003')],
        [],
      );
      expect(result.dagGraph?.tasks).toHaveLength(3);
      expect(result.dagTaskStatus.task_002).toBe('pending');
      expect(result.dagTaskStatus.task_003).toBe('pending');
    });

    it('test_plan_delta_when_skip_tasks_should_set_skipped_status', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001'), makeTask('task_002')]),
        dagTaskStatus: { task_001: 'pending', task_002: 'pending' },
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(state, [], ['task_001']);
      expect(result.dagTaskStatus.task_001).toBe('skipped');
      expect(result.dagTaskStatus.task_002).toBe('pending');
    });

    it('test_plan_delta_when_add_and_skip_should_apply_both', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001'), makeTask('task_002')]),
        dagTaskStatus: { task_001: 'completed', task_002: 'pending' },
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(
        state,
        [makeTask('task_003', 'backend_developer', 'Replacement', ['task_001'])],
        ['task_002'],
      );
      expect(result.dagGraph?.tasks).toHaveLength(3);
      expect(result.dagTaskStatus.task_002).toBe('skipped');
      expect(result.dagTaskStatus.task_003).toBe('pending');
    });

    it('test_plan_delta_when_duplicate_add_should_not_duplicate', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001')]),
        dagTaskStatus: { task_001: 'completed' },
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(state, [makeTask('task_001')], []);
      expect(result.dagGraph?.tasks).toHaveLength(1);
    });

    it('test_plan_delta_should_preserve_existing_completed_statuses', () => {
      const state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001'), makeTask('task_002')]),
        dagTaskStatus: { task_001: 'completed', task_002: 'failed' },
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(state, [makeTask('task_003')], []);
      expect(result.dagTaskStatus.task_001).toBe('completed');
      expect(result.dagTaskStatus.task_002).toBe('failed');
    });

    it('test_plan_delta_when_no_existing_graph_should_create_from_add_tasks', () => {
      const state: SimulatedState = {
        dagGraph: null,
        dagTaskStatus: {},
        dagTaskFailureReasons: {},
      };
      const result = simulatePlanDelta(
        state,
        [makeTask('task_001'), makeTask('task_002')],
        [],
      );
      expect(result.dagGraph?.tasks).toHaveLength(2);
      expect(result.dagTaskStatus.task_001).toBe('pending');
    });

    it('test_plan_delta_when_concurrent_deltas_should_accumulate', () => {
      let state: SimulatedState = {
        dagGraph: makeDagGraph([makeTask('task_001')]),
        dagTaskStatus: { task_001: 'completed' },
        dagTaskFailureReasons: {},
      };

      // First delta
      state = simulatePlanDelta(state, [makeTask('task_002')], []);
      // Second delta
      state = simulatePlanDelta(state, [makeTask('task_003')], ['task_002']);

      expect(state.dagGraph?.tasks).toHaveLength(3);
      expect(state.dagTaskStatus.task_001).toBe('completed');
      expect(state.dagTaskStatus.task_002).toBe('skipped');
      expect(state.dagTaskStatus.task_003).toBe('pending');
    });
  });
});

// ============================================================================
// PlanView rendering states (via dagToPlanSteps + computeProgress)
// ============================================================================

describe('PlanView rendering states', () => {
  it('test_skipped_tasks_should_have_skipped_status_in_steps', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      makeTask('task_003'),
    ]);
    const status = {
      task_001: 'completed' as const,
      task_002: 'skipped' as const,
      task_003: 'pending' as const,
    };
    const steps = dagToPlanSteps(graph, status, {});
    expect(steps[1].status).toBe('skipped');
    const progress = computeProgress(steps);
    expect(progress.skipped).toBe(1);
    expect(progress.actionable).toBe(2);
  });

  it('test_mixed_statuses_should_compute_correct_progress', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      makeTask('task_003'),
      makeTask('task_004'),
      makeTask('task_005'),
    ]);
    const status = {
      task_001: 'completed' as const,
      task_002: 'completed' as const,
      task_003: 'skipped' as const,
      task_004: 'failed' as const,
      task_005: 'working' as const,
    };
    const steps = dagToPlanSteps(graph, status, { task_004: 'Build failed' });
    const progress = computeProgress(steps);
    expect(progress.total).toBe(5);
    expect(progress.completed).toBe(2);
    expect(progress.skipped).toBe(1);
    expect(progress.failed).toBe(1);
    expect(progress.inProgress).toBe(1);
    expect(progress.actionable).toBe(4);
    expect(progress.isAllDone).toBe(false);
    expect(progress.hasFailures).toBe(true);
    expect(progress.pct).toBe(50);
  });

  it('test_celebration_trigger_when_all_non_skipped_done_should_fire', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      makeTask('task_003'),
    ]);
    const status = {
      task_001: 'completed' as const,
      task_002: 'skipped' as const,
      task_003: 'completed' as const,
    };
    const steps = dagToPlanSteps(graph, status, {});
    const progress = computeProgress(steps);
    expect(progress.isAllDone).toBe(true);
  });

  it('test_grouping_with_skipped_tasks_across_rounds', () => {
    const graph = makeDagGraph([
      makeTask('task_001'),
      makeTask('task_002'),
      // Gap to trigger round 2
      makeTask('task_005'),
      makeTask('task_006'),
    ]);
    const status = {
      task_001: 'completed' as const,
      task_002: 'skipped' as const,
      task_005: 'pending' as const,
      task_006: 'working' as const,
    };
    const steps = dagToPlanSteps(graph, status, {});
    const groups = groupStepsByRound(steps);

    expect(groups).toHaveLength(2);
    // Round 1: one done, one skipped
    expect(groups[0].steps).toHaveLength(2);
    expect(groups[0].steps[0].status).toBe('done');
    expect(groups[0].steps[1].status).toBe('skipped');
    // Round 2: one pending, one in_progress
    expect(groups[1].steps).toHaveLength(2);
  });
});
