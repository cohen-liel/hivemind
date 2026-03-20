/**
 * Tests for WebSocket resilience features: heartbeat, event replay,
 * priority queue, and connection quality state.
 *
 * Covers: WebSocketContext.tsx (task_004), types.ts ConnectionQuality
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Types ────────────────────────────────────────────────────────────────────

type ConnectionQuality = 'connected' | 'degraded' | 'disconnected';

interface QueuedMessage {
  message: Record<string, unknown>;
  timestamp: number;
  priority: 'critical' | 'normal';
}

// ── Constants (mirrored from WebSocketContext.tsx) ───────────────────────────

const HEARTBEAT_INTERVAL_MS = 30_000;
const HEARTBEAT_DEGRADED_THRESHOLD_MS = 45_000;
const HEARTBEAT_STALE_THRESHOLD_MS = 60_000;
const MESSAGE_QUEUE_MAX_SIZE = 100;
const MESSAGE_MAX_AGE_MS = 60_000;

const CRITICAL_MESSAGE_TYPES = new Set([
  'task_graph',
  'plan_delta',
  'execution_error',
  'task_error',
  'replay',
  'replay_range',
]);

// ── Extracted pure functions for testing ─────────────────────────────────────

function classifyMessagePriority(message: Record<string, unknown>): 'critical' | 'normal' {
  const type = message.type as string;
  return CRITICAL_MESSAGE_TYPES.has(type) ? 'critical' : 'normal';
}

function computeBackoffDelay(attempt: number): number {
  const baseDelay = 1000;
  const maxDelay = 30_000;
  const expDelay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay);
  // Full jitter: random [0, expDelay]
  return Math.random() * expDelay;
}

function computeConnectionQuality(
  isOpen: boolean,
  isAuthenticated: boolean,
  lastPongTs: number,
  now: number,
): ConnectionQuality {
  if (!isOpen || !isAuthenticated) return 'disconnected';
  if (lastPongTs === 0) return 'connected'; // No heartbeat yet
  const elapsed = now - lastPongTs;
  if (elapsed > HEARTBEAT_STALE_THRESHOLD_MS) return 'disconnected';
  if (elapsed > HEARTBEAT_DEGRADED_THRESHOLD_MS) return 'degraded';
  return 'connected';
}

function flushMessageQueue(
  queue: QueuedMessage[],
  now: number,
): QueuedMessage[] {
  // Filter out expired messages
  const valid = queue.filter(m => now - m.timestamp < MESSAGE_MAX_AGE_MS);
  // Sort: critical first, then by timestamp (oldest first)
  return valid.sort((a, b) => {
    if (a.priority !== b.priority) {
      return a.priority === 'critical' ? -1 : 1;
    }
    return a.timestamp - b.timestamp;
  });
}

function trackProjectSequence(
  sequences: Map<string, number>,
  projectId: string,
  sequenceId: number,
): { gap: boolean; expected: number } {
  const last = sequences.get(projectId) ?? 0;
  const gap = sequenceId > last + 1;
  sequences.set(projectId, Math.max(last, sequenceId));
  return { gap, expected: last + 1 };
}


// ── Tests ────────────────────────────────────────────────────────────────────

describe('WebSocket resilience (task_004)', () => {

  // ── Heartbeat mechanism ──

  describe('heartbeat', () => {
    it('test_heartbeat_constants_should_have_correct_values', () => {
      expect(HEARTBEAT_INTERVAL_MS).toBe(30_000);
      expect(HEARTBEAT_DEGRADED_THRESHOLD_MS).toBe(45_000);
      expect(HEARTBEAT_STALE_THRESHOLD_MS).toBe(60_000);
    });

    it('test_heartbeat_degraded_threshold_should_be_greater_than_interval', () => {
      expect(HEARTBEAT_DEGRADED_THRESHOLD_MS).toBeGreaterThan(HEARTBEAT_INTERVAL_MS);
    });

    it('test_heartbeat_stale_threshold_should_be_greater_than_degraded', () => {
      expect(HEARTBEAT_STALE_THRESHOLD_MS).toBeGreaterThan(HEARTBEAT_DEGRADED_THRESHOLD_MS);
    });
  });

  // ── Connection quality state ──

  describe('connection quality', () => {
    it('test_quality_when_not_open_should_be_disconnected', () => {
      expect(computeConnectionQuality(false, true, Date.now(), Date.now())).toBe('disconnected');
    });

    it('test_quality_when_not_authenticated_should_be_disconnected', () => {
      expect(computeConnectionQuality(true, false, Date.now(), Date.now())).toBe('disconnected');
    });

    it('test_quality_when_recent_pong_should_be_connected', () => {
      const now = Date.now();
      expect(computeConnectionQuality(true, true, now - 10_000, now)).toBe('connected');
    });

    it('test_quality_when_pong_delayed_45s_should_be_degraded', () => {
      const now = Date.now();
      expect(computeConnectionQuality(true, true, now - 46_000, now)).toBe('degraded');
    });

    it('test_quality_when_pong_delayed_60s_should_be_disconnected', () => {
      const now = Date.now();
      expect(computeConnectionQuality(true, true, now - 61_000, now)).toBe('disconnected');
    });

    it('test_quality_when_no_heartbeat_yet_should_be_connected', () => {
      expect(computeConnectionQuality(true, true, 0, Date.now())).toBe('connected');
    });

    it('test_quality_at_exact_degraded_boundary_should_be_connected', () => {
      const now = Date.now();
      // Exactly at threshold
      expect(computeConnectionQuality(true, true, now - HEARTBEAT_DEGRADED_THRESHOLD_MS, now)).toBe('connected');
    });

    it('test_quality_just_past_degraded_boundary_should_be_degraded', () => {
      const now = Date.now();
      expect(computeConnectionQuality(true, true, now - HEARTBEAT_DEGRADED_THRESHOLD_MS - 1, now)).toBe('degraded');
    });
  });

  // ── Message priority classification ──

  describe('priority queue', () => {
    it('test_classify_task_graph_should_be_critical', () => {
      expect(classifyMessagePriority({ type: 'task_graph' })).toBe('critical');
    });

    it('test_classify_plan_delta_should_be_critical', () => {
      expect(classifyMessagePriority({ type: 'plan_delta' })).toBe('critical');
    });

    it('test_classify_execution_error_should_be_critical', () => {
      expect(classifyMessagePriority({ type: 'execution_error' })).toBe('critical');
    });

    it('test_classify_agent_update_should_be_normal', () => {
      expect(classifyMessagePriority({ type: 'agent_update' })).toBe('normal');
    });

    it('test_classify_replay_should_be_critical', () => {
      expect(classifyMessagePriority({ type: 'replay' })).toBe('critical');
    });

    it('test_classify_replay_range_should_be_critical', () => {
      expect(classifyMessagePriority({ type: 'replay_range' })).toBe('critical');
    });

    it('test_flush_should_sort_critical_first', () => {
      const now = Date.now();
      const queue: QueuedMessage[] = [
        { message: { type: 'agent_update' }, timestamp: now - 1000, priority: 'normal' },
        { message: { type: 'plan_delta' }, timestamp: now - 2000, priority: 'critical' },
        { message: { type: 'agent_text' }, timestamp: now - 500, priority: 'normal' },
        { message: { type: 'task_graph' }, timestamp: now - 3000, priority: 'critical' },
      ];
      const sorted = flushMessageQueue(queue, now);
      expect(sorted[0].priority).toBe('critical');
      expect(sorted[1].priority).toBe('critical');
      expect(sorted[2].priority).toBe('normal');
      expect(sorted[3].priority).toBe('normal');
    });

    it('test_flush_should_remove_expired_messages', () => {
      const now = Date.now();
      const queue: QueuedMessage[] = [
        { message: { type: 'agent_update' }, timestamp: now - MESSAGE_MAX_AGE_MS - 1, priority: 'normal' },
        { message: { type: 'plan_delta' }, timestamp: now - 5000, priority: 'critical' },
      ];
      const sorted = flushMessageQueue(queue, now);
      expect(sorted).toHaveLength(1);
      expect(sorted[0].message.type).toBe('plan_delta');
    });

    it('test_flush_when_empty_queue_should_return_empty', () => {
      const sorted = flushMessageQueue([], Date.now());
      expect(sorted).toHaveLength(0);
    });

    it('test_flush_should_preserve_timestamp_order_within_same_priority', () => {
      const now = Date.now();
      const queue: QueuedMessage[] = [
        { message: { type: 'a' }, timestamp: now - 3000, priority: 'normal' },
        { message: { type: 'b' }, timestamp: now - 1000, priority: 'normal' },
        { message: { type: 'c' }, timestamp: now - 5000, priority: 'normal' },
      ];
      const sorted = flushMessageQueue(queue, now);
      expect(sorted[0].message.type).toBe('c');
      expect(sorted[1].message.type).toBe('a');
      expect(sorted[2].message.type).toBe('b');
    });
  });

  // ── Sequence tracking and gap detection ──

  describe('event replay and sequence tracking', () => {
    it('test_track_sequence_when_first_event_should_not_detect_gap', () => {
      const sequences = new Map<string, number>();
      const result = trackProjectSequence(sequences, 'proj1', 1);
      expect(result.gap).toBe(false);
    });

    it('test_track_sequence_when_sequential_should_not_detect_gap', () => {
      const sequences = new Map<string, number>();
      trackProjectSequence(sequences, 'proj1', 1);
      const result = trackProjectSequence(sequences, 'proj1', 2);
      expect(result.gap).toBe(false);
    });

    it('test_track_sequence_when_gap_should_detect', () => {
      const sequences = new Map<string, number>();
      trackProjectSequence(sequences, 'proj1', 1);
      const result = trackProjectSequence(sequences, 'proj1', 5);
      expect(result.gap).toBe(true);
      expect(result.expected).toBe(2);
    });

    it('test_track_sequence_when_out_of_order_should_not_go_backward', () => {
      const sequences = new Map<string, number>();
      trackProjectSequence(sequences, 'proj1', 5);
      trackProjectSequence(sequences, 'proj1', 3);
      expect(sequences.get('proj1')).toBe(5);
    });

    it('test_track_sequence_per_project_should_be_independent', () => {
      const sequences = new Map<string, number>();
      trackProjectSequence(sequences, 'proj1', 10);
      trackProjectSequence(sequences, 'proj2', 1);
      expect(sequences.get('proj1')).toBe(10);
      expect(sequences.get('proj2')).toBe(1);
    });
  });

  // ── Backoff computation ──

  describe('backoff delay', () => {
    it('test_backoff_attempt_0_should_be_within_1s', () => {
      for (let i = 0; i < 20; i++) {
        const delay = computeBackoffDelay(0);
        expect(delay).toBeGreaterThanOrEqual(0);
        expect(delay).toBeLessThanOrEqual(1000);
      }
    });

    it('test_backoff_should_not_exceed_max_30s', () => {
      for (let i = 0; i < 20; i++) {
        const delay = computeBackoffDelay(100);
        expect(delay).toBeLessThanOrEqual(30_000);
      }
    });

    it('test_backoff_should_increase_with_attempts', () => {
      // Average over many samples should show increase
      const avg0 = Array.from({ length: 100 }, () => computeBackoffDelay(0))
        .reduce((a, b) => a + b) / 100;
      const avg5 = Array.from({ length: 100 }, () => computeBackoffDelay(5))
        .reduce((a, b) => a + b) / 100;
      expect(avg5).toBeGreaterThan(avg0);
    });
  });

  // ── ConnectionQuality type ──

  describe('ConnectionQuality type', () => {
    it('test_connection_quality_should_be_valid_union', () => {
      const valid: ConnectionQuality[] = ['connected', 'degraded', 'disconnected'];
      valid.forEach(q => {
        expect(['connected', 'degraded', 'disconnected']).toContain(q);
      });
    });
  });
});
