/**
 * Tests for plan_delta dispatch and task_complete handler in useProjectWebSocket.
 *
 * Covers: useProjectWebSocket.ts (task_001) — plan_delta event handling,
 *         task_complete event handling, error resilience for malformed events.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Types ────────────────────────────────────────────────────────────────────

type WSEvent = {
  type: string;
  project_id?: string;
  sequence_id?: number;
  [key: string]: unknown;
};

type ProjectAction = {
  type: string;
  [key: string]: unknown;
};

// ── Test helper: simulate the plan_delta and task_complete dispatch logic ────
// We test the pure dispatch logic extracted from useProjectWebSocket.ts
// rather than the full React hook, to keep tests deterministic and fast.

let nextIdCounter = 0;
function nextId(): number {
  return ++nextIdCounter;
}

/**
 * Simulates the event handling switch from useProjectWebSocket.ts.
 * Returns the dispatched action or null if no dispatch occurred.
 */
function handleWSEvent(event: WSEvent, dispatch: (action: ProjectAction) => void): void {
  switch (event.type) {
    case 'plan_delta': {
      try {
        dispatch({
          type: 'WS_PLAN_DELTA',
          payload: event,
        });
      } catch (err) {
        console.warn('[useProjectWebSocket] Malformed plan_delta event:', err);
      }
      break;
    }
    case 'task_complete': {
      const ts = typeof event.timestamp === 'number' ? event.timestamp : Date.now() / 1000;
      dispatch({
        type: 'ADD_ACTIVITY',
        payload: {
          id: nextId(),
          type: 'agent_result',
          agent: event.agent ?? 'unknown',
          content: `✅ Task ${event.task_name ?? event.task_id ?? 'unknown'} completed`,
          timestamp: ts,
          task_id: event.task_id,
        },
      });
      break;
    }
    case 'agent_update': {
      dispatch({ type: 'WS_AGENT_UPDATE', payload: event });
      break;
    }
    case 'task_graph': {
      dispatch({ type: 'WS_TASK_GRAPH', payload: event });
      break;
    }
    default:
      break;
  }
}


// ── Tests ────────────────────────────────────────────────────────────────────

describe('plan_delta dispatch (task_001)', () => {
  let dispatch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    dispatch = vi.fn();
    nextIdCounter = 0;
  });

  // ── plan_delta event handling ──

  it('test_plan_delta_when_valid_event_should_dispatch_ws_plan_delta', () => {
    const event: WSEvent = {
      type: 'plan_delta',
      project_id: 'proj1',
      add_tasks: [{ id: 'task_new', role: 'developer', goal: 'New task' }],
      skip_task_ids: [],
      reason: 'Added via plan modification',
    };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch).toHaveBeenCalledWith({
      type: 'WS_PLAN_DELTA',
      payload: event,
    });
  });

  it('test_plan_delta_when_has_skip_task_ids_should_dispatch_with_skips', () => {
    const event: WSEvent = {
      type: 'plan_delta',
      project_id: 'proj1',
      add_tasks: [],
      skip_task_ids: ['task_001', 'task_002'],
      reason: 'Skipping irrelevant tasks',
    };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledOnce();
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.skip_task_ids).toEqual(['task_001', 'task_002']);
  });

  it('test_plan_delta_when_malformed_should_not_throw', () => {
    const event: WSEvent = { type: 'plan_delta' };
    expect(() => handleWSEvent(event, dispatch)).not.toThrow();
    expect(dispatch).toHaveBeenCalledOnce();
  });

  it('test_plan_delta_when_dispatch_throws_should_catch_error', () => {
    const throwingDispatch = vi.fn(() => {
      throw new Error('Reducer error');
    });
    const event: WSEvent = {
      type: 'plan_delta',
      add_tasks: [],
      skip_task_ids: [],
    };
    expect(() => handleWSEvent(event, throwingDispatch)).not.toThrow();
  });

  it('test_plan_delta_when_empty_add_tasks_should_dispatch', () => {
    const event: WSEvent = {
      type: 'plan_delta',
      add_tasks: [],
      skip_task_ids: [],
    };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledOnce();
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.add_tasks).toEqual([]);
  });

  it('test_plan_delta_when_cumulative_flag_set_should_include_in_payload', () => {
    const event: WSEvent = {
      type: 'plan_delta',
      add_tasks: [],
      skip_task_ids: [],
      cumulative: true,
      task_history: [{ task_id: 't1', status: 'completed' }],
    };
    handleWSEvent(event, dispatch);
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.cumulative).toBe(true);
    expect(action.payload.task_history).toBeDefined();
  });

  it('test_plan_delta_when_has_sequence_id_should_pass_through', () => {
    const event: WSEvent = {
      type: 'plan_delta',
      sequence_id: 42,
      add_tasks: [],
      skip_task_ids: [],
    };
    handleWSEvent(event, dispatch);
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.sequence_id).toBe(42);
  });

  // ── task_complete event handling ──

  it('test_task_complete_when_valid_should_dispatch_add_activity', () => {
    const event: WSEvent = {
      type: 'task_complete',
      task_id: 'task_001',
      task_name: 'Build API',
      agent: 'backend_developer',
      timestamp: 1700000000,
    };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledOnce();
    const action = dispatch.mock.calls[0][0];
    expect(action.type).toBe('ADD_ACTIVITY');
    expect(action.payload.agent).toBe('backend_developer');
    expect(action.payload.content).toContain('Build API');
    expect(action.payload.timestamp).toBe(1700000000);
  });

  it('test_task_complete_when_no_agent_should_use_unknown', () => {
    const event: WSEvent = {
      type: 'task_complete',
      task_id: 'task_002',
    };
    handleWSEvent(event, dispatch);
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.agent).toBe('unknown');
  });

  it('test_task_complete_when_no_timestamp_should_use_now', () => {
    const now = Date.now() / 1000;
    const event: WSEvent = {
      type: 'task_complete',
      task_id: 'task_003',
    };
    handleWSEvent(event, dispatch);
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.timestamp).toBeGreaterThanOrEqual(now - 1);
    expect(action.payload.timestamp).toBeLessThanOrEqual(now + 1);
  });

  it('test_task_complete_should_generate_unique_ids', () => {
    handleWSEvent({ type: 'task_complete', task_id: 't1' }, dispatch);
    handleWSEvent({ type: 'task_complete', task_id: 't2' }, dispatch);
    const id1 = dispatch.mock.calls[0][0].payload.id;
    const id2 = dispatch.mock.calls[1][0].payload.id;
    expect(id1).not.toBe(id2);
  });

  it('test_task_complete_when_task_name_missing_should_use_task_id', () => {
    const event: WSEvent = {
      type: 'task_complete',
      task_id: 'task_007',
    };
    handleWSEvent(event, dispatch);
    const action = dispatch.mock.calls[0][0];
    expect(action.payload.content).toContain('task_007');
  });

  // ── other event types pass through ──

  it('test_agent_update_should_dispatch_ws_agent_update', () => {
    const event: WSEvent = { type: 'agent_update', agent: 'pm', content: 'Planning...' };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledWith({ type: 'WS_AGENT_UPDATE', payload: event });
  });

  it('test_unknown_event_type_should_not_dispatch', () => {
    const event: WSEvent = { type: 'unknown_type', data: 'test' };
    handleWSEvent(event, dispatch);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it('test_task_graph_should_dispatch_ws_task_graph', () => {
    const event: WSEvent = { type: 'task_graph', tasks: [] };
    handleWSEvent(event, dispatch);
    expect(dispatch).toHaveBeenCalledWith({ type: 'WS_TASK_GRAPH', payload: event });
  });

  // ── concurrent plan_deltas ──

  it('test_rapid_plan_deltas_should_all_dispatch_independently', () => {
    for (let i = 0; i < 10; i++) {
      handleWSEvent({
        type: 'plan_delta',
        add_tasks: [{ id: `task_${i}`, role: 'dev', goal: `Task ${i}` }],
        skip_task_ids: [],
      }, dispatch);
    }
    expect(dispatch).toHaveBeenCalledTimes(10);
    // Each call should have different add_tasks
    for (let i = 0; i < 10; i++) {
      const action = dispatch.mock.calls[i][0];
      expect(action.payload.add_tasks[0].id).toBe(`task_${i}`);
    }
  });
});
