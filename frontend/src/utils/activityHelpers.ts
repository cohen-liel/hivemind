/**
 * activityHelpers.ts — Data transformation utilities for ProjectView.
 *
 * Converts raw API responses (messages + activity events) into
 * the unified ActivityEntry / SdkCall / AgentState structures
 * consumed by the UI components.
 */

import type { ProjectMessage, ActivityEntry, AgentState as AgentStateType, ActivityEvent } from '../types';
import type { SdkCall } from '../reducers/projectReducer';

/** Generate a unique ID for activity entries.
 *  crypto.randomUUID() requires a secure context (HTTPS / localhost).
 *  On plain HTTP (LAN dev), we fall back to a Math.random UUID v4. */
export function nextId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/** Convert persisted message rows from the messages table into ActivityEntry objects. */
export function messagesToActivities(messages: ProjectMessage[]): ActivityEntry[] {
  return messages.map((msg, idx) => ({
    // Include array index as tiebreaker to prevent ID collisions when two
    // messages from the same agent arrive within the same second.
    id: `msg-${msg.timestamp}-${msg.agent_name}-${idx}`,
    type: msg.agent_name === 'user' ? 'user_message' as const : 'agent_text' as const,
    timestamp: msg.timestamp,
    agent: msg.agent_name,
    content: msg.content,
    cost: msg.cost_usd,
  }));
}

/** Convert persisted activity events from DB into ActivityEntry objects for the feed. */
export function activityEventsToEntries(events: ActivityEvent[]): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  // Track last summary per agent to deduplicate agent_update events (same
  // logic the live reducer uses — only include new, meaningful summaries).
  const lastSummaryByAgent = new Map<string, string>();
  for (let i = 0; i < events.length; i++) {
    const evt = events[i];
    // Prefer sequence_id (monotonically unique per project in the DB).
    // Fall back to timestamp+index to prevent collisions when multiple events
    // land in the same second without a sequence_id.
    const stableId = evt.sequence_id != null
      ? `act-${evt.sequence_id}`
      : `act-${evt.timestamp}-${i}`;

    switch (evt.type) {
      case 'tool_use':
        entries.push({
          id: stableId,
          type: 'tool_use',
          timestamp: evt.timestamp,
          agent: evt.agent,
          tool_name: evt.tool_name,
          tool_description: evt.description,
        });
        break;
      case 'agent_started':
        entries.push({
          id: stableId,
          type: 'agent_started',
          timestamp: evt.timestamp,
          agent: evt.agent,
          task: evt.task,
        });
        break;
      case 'agent_finished':
        entries.push({
          id: stableId,
          type: 'agent_finished',
          timestamp: evt.timestamp,
          agent: evt.agent,
          cost: evt.cost,
          turns: evt.turns,
          duration: evt.duration,
          is_error: evt.is_error,
        });
        break;
      case 'delegation':
        entries.push({
          id: stableId,
          type: 'delegation',
          timestamp: evt.timestamp,
          agent: evt.agent,
          from_agent: evt.from_agent,
          to_agent: evt.to_agent,
          task: evt.task,
        });
        break;
      case 'loop_progress':
        entries.push({
          id: stableId,
          type: 'loop_progress',
          timestamp: evt.timestamp,
          loop: evt.loop,
          max_loops: evt.max_loops,
          turn: evt.turn,
          max_turns: evt.max_turns,
          max_budget: evt.max_budget,
          cost: evt.cost,
        });
        break;
      case 'task_error':
        entries.push({
          id: stableId,
          type: 'error',
          timestamp: evt.timestamp,
          agent: evt.agent,
          content: evt.text || evt.summary,
        });
        break;
      case 'agent_update': {
        // Reconstruct agent text entries from persisted agent_update events.
        // Apply the same dedup/filtering as the live reducer: only include
        // summaries > 25 chars that differ from the previous one for the agent.
        const summary = evt.summary || evt.text || '';
        if (summary.length > 25) {
          const agentKey = evt.agent || '';
          const prev = lastSummaryByAgent.get(agentKey);
          if (summary !== prev) {
            lastSummaryByAgent.set(agentKey, summary);
            entries.push({
              id: stableId,
              type: 'agent_text',
              timestamp: evt.timestamp,
              agent: evt.agent,
              content: summary,
            });
          }
        }
        break;
      }
      // Skip agent_result, agent_final, project_status —
      // agent_result/agent_final are duplicated in messages table,
      // project_status is a state transition (not content)
    }
  }
  return entries;
}

/** Reconstruct DAG task statuses from persisted activity events (for refresh recovery). */
export function reconstructDagTaskStatus(
  events: ActivityEvent[],
): Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'> {
  const statuses: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'> = {};
  for (const evt of events) {
    if (evt.type === 'dag_task_update' && evt.task_id && evt.status) {
      const s = evt.status;
      statuses[evt.task_id] =
        s === 'completed' ? 'completed' :
        s === 'working' ? 'working' :
        s === 'failed' ? 'failed' :
        s === 'cancelled' ? 'cancelled' :
        'pending';
    }
  }
  return statuses;
}

/** Reconstruct SdkCall entries from persisted agent_started/agent_finished events. */
export function reconstructSdkCalls(events: ActivityEvent[]): SdkCall[] {
  const calls: SdkCall[] = [];
  const openCalls = new Map<string, number>(); // agent -> index in calls array

  for (const evt of events) {
    if (evt.type === 'agent_started' && evt.agent) {
      const idx = calls.length;
      calls.push({
        agent: evt.agent,
        startTime: evt.timestamp,
        status: 'completed', // assume completed since it's historical
      });
      openCalls.set(evt.agent, idx);
    } else if (evt.type === 'agent_finished' && evt.agent) {
      const idx = openCalls.get(evt.agent);
      if (idx !== undefined) {
        calls[idx].endTime = evt.timestamp;
        calls[idx].cost = evt.cost;
        calls[idx].status = evt.is_error ? 'error' : 'completed';
        openCalls.delete(evt.agent);
      }
    }
  }

  return calls;
}

/** Reconstruct last-known agent states from persisted activity events (for refresh recovery). */
export function reconstructAgentStates(events: ActivityEvent[]): Record<string, AgentStateType> {
  const states: Record<string, AgentStateType> = {};

  for (const evt of events) {
    if (evt.type === 'agent_started' && evt.agent) {
      states[evt.agent] = {
        name: evt.agent,
        state: 'working',
        task: evt.task,
        cost: states[evt.agent]?.cost ?? 0,
        turns: states[evt.agent]?.turns ?? 0,
        duration: 0,
        started_at: evt.timestamp * 1000, // convert to ms
        last_update_at: evt.timestamp * 1000,
      };
    } else if (evt.type === 'agent_finished' && evt.agent) {
      states[evt.agent] = {
        name: evt.agent,
        state: evt.is_error ? 'error' : 'done',
        task: states[evt.agent]?.task,
        cost: (states[evt.agent]?.cost ?? 0) + (evt.cost ?? 0),
        turns: (states[evt.agent]?.turns ?? 0) + (evt.turns ?? 0),
        duration: evt.duration ?? 0,
        started_at: undefined,
        last_update_at: evt.timestamp * 1000,
      };
    } else if (evt.type === 'delegation' && evt.to_agent) {
      // Mark delegated agent as working with its task
      states[evt.to_agent] = {
        ...states[evt.to_agent] ?? { name: evt.to_agent, cost: 0, turns: 0, duration: 0 },
        name: evt.to_agent,
        state: 'working',
        task: evt.task,
        delegated_from: evt.from_agent,
        started_at: evt.timestamp * 1000,
        last_update_at: evt.timestamp * 1000,
      };
    }
  }

  return states;
}
