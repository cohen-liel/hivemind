/**
 * projectReducerHardening.test.ts — Tests for projectReducer resilience features:
 *
 * 1. Event sequence validation (out-of-order skip, gap warning, no-seq passthrough)
 * 2. State size monitoring (1MB threshold warning, throttling)
 * 3. Error boundary (malformed events return state unchanged)
 *
 * Task: task_008
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  projectReducer,
  initialProjectState,
  type ProjectState,
  type ProjectAction,
} from '../reducers/projectReducer';
import type { WSEvent } from '../types';

// ============================================================================
// Helpers
// ============================================================================

function stateWith(overrides: Partial<ProjectState>): ProjectState {
  return { ...initialProjectState, ...overrides };
}

function makeWSEvent(overrides: Partial<WSEvent> = {}): WSEvent {
  return {
    type: 'agent_update',
    project_id: 'proj_1',
    agent: 'backend_developer',
    text: '*backend_developer* working on task',
    timestamp: Date.now() / 1000,
    ...overrides,
  } as WSEvent;
}

// ============================================================================
// Event Sequence Validation
// ============================================================================

describe('Event sequence validation', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it('test_reducer_when_event_has_higher_sequence_should_process_normally', () => {
    const state = stateWith({ lastSequenceId: 5 });
    const event = makeWSEvent({ sequence_id: 6 });
    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });

    expect(result.lastSequenceId).toBe(6);
    // The event should have been processed — agent states updated
    expect(result.agentStates['backend_developer']).toBeDefined();
    expect(result.agentStates['backend_developer'].state).toBe('working');
  });

  it('test_reducer_when_event_has_equal_sequence_should_skip', () => {
    const state = stateWith({ lastSequenceId: 10 });
    const event = makeWSEvent({ sequence_id: 10 });
    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });

    // State should be unchanged (event skipped)
    expect(result).toBe(state);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Out-of-order event skipped'),
    );
  });

  it('test_reducer_when_event_has_lower_sequence_should_skip', () => {
    const state = stateWith({ lastSequenceId: 10 });
    const event = makeWSEvent({ sequence_id: 3 });
    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });

    expect(result).toBe(state);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Out-of-order event skipped'),
    );
  });

  it('test_reducer_when_sequence_gap_detected_should_warn_but_process', () => {
    const state = stateWith({ lastSequenceId: 5 });
    // Jump from 5 to 8 — missing 6 and 7
    const event = makeWSEvent({ sequence_id: 8 });
    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });

    expect(result.lastSequenceId).toBe(8);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Sequence gap detected'),
    );
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('missed 2 events'),
    );
  });

  it('test_reducer_when_event_has_no_sequence_id_should_process', () => {
    const state = stateWith({ lastSequenceId: 10 });
    const event = makeWSEvent({}); // No sequence_id field
    delete (event as unknown as Record<string, unknown>).sequence_id;
    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });

    // Should process normally — lastSequenceId unchanged (no seq to track)
    expect(result.lastSequenceId).toBe(10);
    // The event IS processed (not skipped) — agentStates should be updated
    expect(result.agentStates['backend_developer']).toBeDefined();
    expect(result.agentStates['backend_developer'].state).toBe('working');
  });

  it('test_reducer_when_consecutive_events_in_order_should_track_sequence', () => {
    let state = stateWith({ lastSequenceId: 0 });

    for (let i = 1; i <= 5; i++) {
      const event = makeWSEvent({ sequence_id: i });
      state = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });
    }

    expect(state.lastSequenceId).toBe(5);
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('test_reducer_when_out_of_order_across_different_action_types_should_skip', () => {
    const state = stateWith({ lastSequenceId: 10 });

    // Try WS_TOOL_USE with old sequence
    const toolEvent = makeWSEvent({ sequence_id: 5 }) as WSEvent;
    const result1 = projectReducer(state, { type: 'WS_TOOL_USE', event: toolEvent });
    expect(result1).toBe(state);

    // Try WS_AGENT_STARTED with old sequence
    const startEvent = makeWSEvent({ sequence_id: 8 });
    const result2 = projectReducer(state, { type: 'WS_AGENT_STARTED', event: startEvent });
    expect(result2).toBe(state);

    // Try WS_AGENT_FINISHED with old sequence
    const finishEvent = makeWSEvent({ sequence_id: 9 });
    const result3 = projectReducer(state, { type: 'WS_AGENT_FINISHED', event: finishEvent });
    expect(result3).toBe(state);
  });

  it('test_reducer_when_sequence_validation_on_WS_EXECUTION_ERROR_should_apply', () => {
    const state = stateWith({ lastSequenceId: 5 });
    const event = makeWSEvent({
      sequence_id: 3,
      type: 'execution_error',
      error_message: 'something broke',
      error_type: 'timeout',
    });
    const result = projectReducer(state, { type: 'WS_EXECUTION_ERROR', event });
    // Old sequence — should be skipped
    expect(result).toBe(state);
  });
});

// ============================================================================
// State Size Monitoring
// ============================================================================

describe('State size monitoring', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.useFakeTimers();
  });

  afterEach(() => {
    warnSpy.mockRestore();
    vi.useRealTimers();
  });

  it('test_reducer_when_state_is_small_should_not_warn', () => {
    // Start with enough time elapsed for check to fire
    vi.setSystemTime(new Date('2026-03-20T10:00:00Z'));

    const state = stateWith({});
    const result = projectReducer(state, { type: 'SET_SENDING', sending: true });

    // Small state should not trigger warning
    const sizeWarnings = warnSpy.mock.calls.filter(
      (call: unknown[]) => typeof call[0] === 'string' && call[0].includes('State size warning'),
    );
    expect(sizeWarnings).toHaveLength(0);
    expect(result.sending).toBe(true);
  });

  it('test_reducer_when_state_exceeds_1mb_should_warn', () => {
    // Set time so the throttle check fires
    vi.setSystemTime(new Date('2026-03-20T10:00:00Z'));

    // Create a state with > 1MB of data
    const bigActivities = Array.from({ length: 5000 }, (_, i) => ({
      id: `act_${i}`,
      text: 'A'.repeat(300), // ~300 bytes each × 5000 = ~1.5MB
      timestamp: Date.now() / 1000,
    }));

    const state = stateWith({ activities: bigActivities as any[] });

    // Advance time to ensure check interval passed
    vi.advanceTimersByTime(6000);

    // Trigger the reducer (action doesn't matter much, just needs to run checkStateSize)
    projectReducer(state, { type: 'SET_SENDING', sending: true });

    const sizeWarnings = warnSpy.mock.calls.filter(
      (call: unknown[]) => typeof call[0] === 'string' && call[0].includes('State size warning'),
    );
    expect(sizeWarnings.length).toBeGreaterThanOrEqual(1);
    expect(sizeWarnings[0][0]).toContain('exceeds 1MB threshold');
  });

  it('test_reducer_when_state_size_check_throttled_should_not_warn_twice_quickly', () => {
    vi.setSystemTime(new Date('2026-03-20T10:00:00Z'));

    const bigActivities = Array.from({ length: 5000 }, (_, i) => ({
      id: `act_${i}`,
      text: 'A'.repeat(300),
      timestamp: Date.now() / 1000,
    }));

    const state = stateWith({ activities: bigActivities as any[] });

    // First call — triggers check
    vi.advanceTimersByTime(6000);
    projectReducer(state, { type: 'SET_SENDING', sending: true });

    const firstCount = warnSpy.mock.calls.filter(
      (call: unknown[]) => typeof call[0] === 'string' && call[0].includes('State size warning'),
    ).length;

    // Second call within throttle interval — should NOT check again
    vi.advanceTimersByTime(1000); // only 1s later
    projectReducer(state, { type: 'SET_SENDING', sending: false });

    const secondCount = warnSpy.mock.calls.filter(
      (call: unknown[]) => typeof call[0] === 'string' && call[0].includes('State size warning'),
    ).length;

    // The warning count should not increase for the second call
    expect(secondCount).toBe(firstCount);
  });
});

// ============================================================================
// Error Boundary (malformed events)
// ============================================================================

describe('Error boundary for malformed events', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it('test_reducer_when_event_causes_exception_should_return_state_unchanged', () => {
    const state = stateWith({ lastSequenceId: 0 });

    // Create a WS_DAG_TASK_UPDATE where accessing task_id property throws.
    // The reducer's try-catch in projectReducer should catch this.
    const maliciousEvent = new Proxy(makeWSEvent({ sequence_id: 1, type: 'dag_task_update' }), {
      get(target, prop) {
        if (prop === 'task_id') return 'task_001'; // pass the guard
        if (prop === 'status') throw new Error('simulated_corrupt_data');
        return (target as any)[prop];
      },
    });

    const result = projectReducer(state, { type: 'WS_DAG_TASK_UPDATE', event: maliciousEvent });

    // Error boundary should catch and return original state
    expect(result).toBe(state);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Error processing action'),
      expect.anything(),
    );
  });

  it('test_reducer_when_WS_DAG_TASK_UPDATE_has_no_task_id_should_return_state', () => {
    const state = stateWith({ lastSequenceId: 0 });
    const event = makeWSEvent({
      sequence_id: 1,
      type: 'dag_task_update',
    });
    // No task_id — reducer should handle gracefully
    delete (event as unknown as Record<string, unknown>).task_id;

    const result = projectReducer(state, { type: 'WS_DAG_TASK_UPDATE', event });

    // Should return state (guard check on taskId)
    expect(result.lastSequenceId).toBeLessThanOrEqual(1);
  });

  it('test_reducer_when_unknown_action_type_should_return_state_unchanged', () => {
    const state = stateWith({});
    // Cast to bypass TypeScript — simulates runtime malformed dispatch
    const action = { type: 'TOTALLY_UNKNOWN_ACTION' } as unknown as ProjectAction;
    const result = projectReducer(state, action);

    // Should hit default case and return state unchanged
    expect(result).toBe(state);
  });

  it('test_reducer_when_WS_AGENT_UPDATE_has_no_agent_should_return_state', () => {
    const state = stateWith({ lastSequenceId: 0 });
    const event = makeWSEvent({
      sequence_id: 1,
      agent: undefined,
      text: 'no agent marker here',
    });

    const result = projectReducer(state, { type: 'WS_AGENT_UPDATE', event });
    // No agent found — early return
    expect(result.activities.length).toBe(0);
  });
});

// ============================================================================
// Integration: Sequence + State together
// ============================================================================

describe('Sequence validation integration', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it('test_reducer_when_replaying_events_with_gaps_and_duplicates_should_handle_correctly', () => {
    let state = stateWith({ lastSequenceId: 0 });

    // Process event 1
    state = projectReducer(state, {
      type: 'WS_AGENT_UPDATE',
      event: makeWSEvent({ sequence_id: 1 }),
    });
    expect(state.lastSequenceId).toBe(1);

    // Skip to event 5 (gap of 3)
    state = projectReducer(state, {
      type: 'WS_AGENT_UPDATE',
      event: makeWSEvent({ sequence_id: 5 }),
    });
    expect(state.lastSequenceId).toBe(5);
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('missed 3 events'));

    // Replay event 3 (out of order — should be skipped)
    const activitiesBeforeReplay = state.activities.length;
    state = projectReducer(state, {
      type: 'WS_AGENT_UPDATE',
      event: makeWSEvent({ sequence_id: 3 }),
    });
    expect(state.lastSequenceId).toBe(5); // unchanged
    expect(state.activities.length).toBe(activitiesBeforeReplay); // no new activity

    // Process event 6 (normal)
    state = projectReducer(state, {
      type: 'WS_AGENT_UPDATE',
      event: makeWSEvent({ sequence_id: 6 }),
    });
    expect(state.lastSequenceId).toBe(6);
  });
});
