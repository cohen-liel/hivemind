/**
 * projectReducer.ts — Centralized state management for ProjectView.
 *
 * Replaces 21 individual useState hooks with a single useReducer.
 * All WebSocket events are handled as discrete, typed dispatch actions.
 * Deduplication uses sequence IDs instead of timestamps.
 */

import type {
  Project,
  FileChanges,
  WSEvent,
  ActivityEntry,
  AgentState as AgentStateType,
  LoopProgress,
} from '../types';

// ============================================================================
// Constants
// ============================================================================

/** Maximum activity entries kept in memory. Prevents unbounded growth during long sessions. */
const MAX_ACTIVITIES = 2000;

/** Append entries to activities, capping at MAX_ACTIVITIES by dropping oldest. */
function appendActivities(existing: ActivityEntry[], ...entries: ActivityEntry[]): ActivityEntry[] {
  const combined = [...existing, ...entries];
  return combined.length > MAX_ACTIVITIES ? combined.slice(-MAX_ACTIVITIES) : combined;
}

// ============================================================================
// Supporting Types
// ============================================================================

export interface SdkCall {
  agent: string;
  startTime: number;
  endTime?: number;
  cost?: number;
  status: string;
  taskId?: string;      // DAG task ID (e.g. "task_003") — distinguishes multiple calls by same role
  taskName?: string;    // Human-readable task name/goal
  turns?: number;       // Number of turns used
  failureReason?: string; // Why the call failed (if status === 'error')
}

export interface HealingEvent {
  timestamp: number;
  failed_task: string;
  failure_category: string;
  remediation_task: string;
  remediation_role: string;
}

export interface LiveAgentEntry {
  text: string;
  tool?: string;
  timestamp: number;
  progress?: string;
}

export type MobileView = 'orchestra' | 'activity' | 'code' | 'changes' | 'plan' | 'trace';
export type DesktopTab = 'hivemind' | 'plan' | 'code' | 'diff' | 'trace';

// ============================================================================
// State
// ============================================================================

export interface ProjectState {
  // Core data
  project: Project | null;
  activities: ActivityEntry[];
  agentStates: Record<string, AgentStateType>;
  loopProgress: LoopProgress | null;
  files: FileChanges | null;
  loadError: string | null;

  // Agent tracking
  sdkCalls: SdkCall[];
  liveAgentStream: Record<string, LiveAgentEntry>;
  lastTicker: string;
  // Deduplication: last summary string per agent (prevents repeated activity entries)
  lastAgentSummaries: Record<string, string>;

  // DAG visualization
  dagGraph: WSEvent['graph'] | null;
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>;
  dagTaskFailureReasons: Record<string, string>;
  healingEvents: HealingEvent[];

  // UI view state
  mobileView: MobileView;
  desktopTab: DesktopTab;
  selectedAgent: string | null;
  showClearConfirm: boolean;

  // Messaging
  sending: boolean;
  messageOffset: number;
  hasMoreMessages: boolean;

  // Misc
  approvalRequest: string | null;

  // Pre-task question surfaced by the orchestrator before dispatching agents
  pendingQuestion: string | null;

  // Sequence-ID-based deduplication (replaces timestamp-based approach)
  lastSequenceId: number;
}

export const initialProjectState: ProjectState = {
  project: null,
  activities: [],
  agentStates: {},
  loopProgress: null,
  files: null,
  loadError: null,
  sdkCalls: [],
  liveAgentStream: {},
  lastTicker: '',
  lastAgentSummaries: {},
  dagGraph: null,
  dagTaskStatus: {},
  dagTaskFailureReasons: {},
  healingEvents: [],
  mobileView: 'orchestra',
  desktopTab: 'hivemind',
  selectedAgent: null,
  showClearConfirm: false,
  sending: false,
  messageOffset: 0,
  hasMoreMessages: false,
  approvalRequest: null,
  pendingQuestion: null,
  lastSequenceId: 0,
};

// ============================================================================
// Actions (Discriminated Union)
// ============================================================================

export type ProjectAction =
  // ── Data loading ──
  | { type: 'SET_PROJECT'; project: Project }
  | { type: 'SET_LOAD_ERROR'; error: string | null }
  | { type: 'SET_FILES'; files: FileChanges | null }
  | { type: 'SET_APPROVAL_REQUEST'; request: string | null }
  | {
      type: 'LOAD_INITIAL_DATA';
      activities: ActivityEntry[];
      sdkCalls: SdkCall[];
      agentStates: Record<string, AgentStateType>;
      dagTaskStatus?: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>;
      hasMoreMessages: boolean;
      messageOffset: number;
      lastSequenceId: number;
    }
  | { type: 'LOAD_EARLIER_MESSAGES'; messages: ActivityEntry[]; newOffset: number; hasMore: boolean }
  | {
      type: 'MERGE_AGENT_STATES_FROM_POLL';
      agentStates: Record<string, {
        state?: string; task?: string; current_tool?: string;
        cost?: number; turns?: number; duration?: number;
      }>;
    }
  | { type: 'MERGE_AGENT_STATES_FROM_LIVE'; restored: Record<string, AgentStateType> }
  | { type: 'RESTORE_LOOP_PROGRESS'; progress: LoopProgress }
  | {
      type: 'HYDRATE_DAG';
      graph: WSEvent['graph'];
      statuses: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>;
    }

  // ── WebSocket events ──
  | { type: 'WS_AGENT_UPDATE'; event: WSEvent }
  | { type: 'WS_TOOL_USE'; event: WSEvent }
  | { type: 'WS_AGENT_STARTED'; event: WSEvent }
  | { type: 'WS_AGENT_FINISHED'; event: WSEvent }
  | { type: 'WS_DELEGATION'; event: WSEvent }
  | { type: 'WS_LOOP_PROGRESS'; event: WSEvent }
  | { type: 'WS_AGENT_RESULT'; event: WSEvent }
  | { type: 'WS_AGENT_FINAL'; event: WSEvent }
  | { type: 'WS_PROJECT_STATUS'; event: WSEvent }
  | { type: 'WS_TASK_GRAPH'; event: WSEvent }
  | { type: 'WS_DAG_TASK_UPDATE'; event: WSEvent }
  | { type: 'WS_EXECUTION_ERROR'; event: WSEvent }
  | { type: 'WS_SELF_HEALING'; event: WSEvent }
  | { type: 'WS_APPROVAL_REQUEST'; event: WSEvent }
  | { type: 'WS_HISTORY_CLEARED' }
  | { type: 'WS_LIVE_STATE_SYNC'; event: WSEvent }
  | { type: 'WS_TURN_PROGRESS'; agent: string; turnsUsed: number; maxTurns: number; remaining: number }
  | { type: 'WS_PRE_TASK_QUESTION'; event: WSEvent }
  | { type: 'CLEAR_PRE_TASK_QUESTION' }

  // ── UI actions ──
  | { type: 'SET_MOBILE_VIEW'; view: MobileView }
  | { type: 'SET_DESKTOP_TAB'; tab: DesktopTab }
  | { type: 'SET_SELECTED_AGENT'; agent: string | null }
  | { type: 'SET_SENDING'; sending: boolean }
  | { type: 'SET_SHOW_CLEAR_CONFIRM'; show: boolean }
  | { type: 'ADD_ACTIVITY'; activity: ActivityEntry }
  | { type: 'CLEAR_ALL_STATE' };

// ============================================================================
// Helpers
// ============================================================================

function nextId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/** Sequence-based deduplication: returns true if event should be added to activities. */
function isNewEvent(state: ProjectState, event: WSEvent): boolean {
  if (event.sequence_id !== undefined) {
    return event.sequence_id > state.lastSequenceId;
  }
  // No sequence_id — allow (backward compat for events without it)
  return true;
}

/** Update lastSequenceId after processing an event. */
function trackSequence(state: ProjectState, event: WSEvent): number {
  if (event.sequence_id !== undefined) {
    return Math.max(state.lastSequenceId, event.sequence_id);
  }
  return state.lastSequenceId;
}

// ============================================================================
// Reducer
// ============================================================================

export function projectReducer(state: ProjectState, action: ProjectAction): ProjectState {
  switch (action.type) {
    // ────────────────────────── Data loading ──────────────────────────

    case 'SET_PROJECT':
      return { ...state, project: action.project };

    case 'SET_LOAD_ERROR':
      return { ...state, loadError: action.error };

    case 'SET_FILES':
      return { ...state, files: action.files };

    case 'SET_APPROVAL_REQUEST':
      return { ...state, approvalRequest: action.request };

    case 'LOAD_INITIAL_DATA': {
      // Merge agent states: only apply restored states where current is idle or absent
      const mergedAgentStates = { ...state.agentStates };
      for (const [name, agentState] of Object.entries(action.agentStates)) {
        if (!mergedAgentStates[name] || mergedAgentStates[name].state === 'idle') {
          mergedAgentStates[name] = agentState;
        }
      }
      // Merge DAG task statuses: only apply if current state is empty (avoids
      // overwriting live WS updates that arrived before the activity load).
      const mergedDagTaskStatus = Object.keys(state.dagTaskStatus).length > 0
        ? state.dagTaskStatus
        : action.dagTaskStatus ?? state.dagTaskStatus;

      return {
        ...state,
        activities: action.activities,
        sdkCalls: action.sdkCalls.length > 0 ? action.sdkCalls : state.sdkCalls,
        agentStates: mergedAgentStates,
        dagTaskStatus: mergedDagTaskStatus,
        hasMoreMessages: action.hasMoreMessages,
        messageOffset: action.messageOffset,
        lastSequenceId: action.lastSequenceId,
      };
    }

    case 'LOAD_EARLIER_MESSAGES':
      return {
        ...state,
        activities: [...action.messages, ...state.activities],
        messageOffset: action.newOffset,
        hasMoreMessages: action.hasMore,
      };

    case 'MERGE_AGENT_STATES_FROM_POLL': {
      // Polling fetches from DB which may lag behind WS events.
      // CRITICAL: Never regress a terminal state (done/error) back to working —
      // WS events are authoritative for state transitions.  The DB batch-writes
      // every ~2s, so a poll can return stale 'working' after WS already set 'done'.
      // Without this guard, the UI flickers back to "working" and gets stuck.
      const TERMINAL_STATES = new Set(['done', 'error']);
      let changed = false;
      const updated = { ...state.agentStates };
      for (const [name, s] of Object.entries(action.agentStates)) {
        const serverState = (s.state ?? 'idle') as AgentStateType['state'];
        const ourState = updated[name]?.state ?? 'idle';

        // GUARD: If we already have a terminal state from WS, don't let
        // a stale poll overwrite it with 'working' or 'idle'
        if (TERMINAL_STATES.has(ourState) && !TERMINAL_STATES.has(serverState)) {
          continue; // Skip — WS already told us this agent finished
        }

        const isActive = serverState === 'working' || serverState === 'waiting';
        const shouldSync =
          isActive
          || (serverState !== 'idle' && ourState !== serverState)
          || (serverState === ourState && s.current_tool && s.current_tool !== updated[name]?.current_tool);
        if (shouldSync) {
          updated[name] = {
            ...updated[name],
            name,
            state: serverState,
            task: s.task ?? updated[name]?.task,
            current_tool: s.current_tool ?? undefined,
            cost: s.cost ?? updated[name]?.cost ?? 0,
            turns: s.turns ?? updated[name]?.turns ?? 0,
            duration: updated[name]?.duration ?? 0,
            started_at: updated[name]?.started_at ?? (isActive ? Date.now() : undefined),
            last_update_at: isActive ? Date.now() : updated[name]?.last_update_at,
          };
          changed = true;
        }
      }
      return changed ? { ...state, agentStates: updated } : state;
    }

    case 'MERGE_AGENT_STATES_FROM_LIVE': {
      // Always merge live state — don't skip when agents are already working.
      // The polling fallback needs this to update agent progress in real-time
      // even when WebSocket is disconnected (critical for mobile).
      // Terminal state protection: don't overwrite agents already in completed/failed/cancelled.
      const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);
      const mergedAgentStates = { ...state.agentStates };
      for (const [agentKey, restoredState] of Object.entries(action.restored)) {
        const existing = state.agentStates[agentKey];
        if (existing && TERMINAL_STATES.has(existing.state as string)) {
          // Preserve terminal state — do not overwrite
          continue;
        }
        mergedAgentStates[agentKey] = restoredState;
      }
      return { ...state, agentStates: mergedAgentStates };
    }

    case 'RESTORE_LOOP_PROGRESS':
      return { ...state, loopProgress: action.progress };

    case 'HYDRATE_DAG':
      return { ...state, dagGraph: action.graph, dagTaskStatus: action.statuses };

    // ────────────────────────── WebSocket events ──────────────────────────

    case 'WS_AGENT_UPDATE': {
      const event = action.event;
      const updateAgent = event.agent || (event.text?.match(/\*(\w+)\*/)?.[1]);
      if (!updateAgent) return state;

      const agentStatus: AgentStateType['state'] =
        event.status === 'error' ? 'error'
        : event.status === 'done' ? 'done'
        : 'working';

      const newAgentStates: Record<string, AgentStateType> = {
        ...state.agentStates,
        [updateAgent]: {
          ...state.agentStates[updateAgent],
          name: updateAgent,
          state: agentStatus,
          current_tool: event.summary || event.text?.slice(0, 150),
          cost: event.cost ?? state.agentStates[updateAgent]?.cost ?? 0,
          last_update_at: Date.now(),
          started_at: state.agentStates[updateAgent]?.started_at ?? (agentStatus === 'working' ? Date.now() : undefined),
        },
      };

      // Update live agent stream
      const liveText = event.summary || event.text || '';
      let newLiveStream = state.liveAgentStream;
      if (liveText && agentStatus === 'working') {
        newLiveStream = {
          ...newLiveStream,
          [updateAgent]: {
            text: liveText.slice(0, 300),
            tool: newLiveStream[updateAgent]?.tool,
            timestamp: Date.now(),
            progress: event.progress,
          },
        };
      }
      // Clean up liveAgentStream when agent transitions to error/done
      if (agentStatus !== 'working') {
        const next = { ...newLiveStream };
        delete next[updateAgent];
        newLiveStream = next;
      }

      // Ticker
      const progressStr = event.progress ? ` (${event.progress})` : '';
      const remStr = event.is_remediation ? ' 🔧' : '';
      const tickerAction = event.summary || event.text?.slice(0, 100) || 'working...';

      // Pipe meaningful agent summaries into the activity log so the chat stays alive.
      // Deduplicate by content (same text seen before = skip).
      let newActivities = state.activities;
      let newLastAgentSummaries = state.lastAgentSummaries;
      const summaryText = event.summary || '';
      if (
        agentStatus === 'working' &&
        summaryText.length > 25 &&                                          // must be meaningful
        summaryText !== state.lastAgentSummaries[updateAgent]              // must be NEW
      ) {
        const icon = updateAgent === 'orchestrator' ? '🎯' :
                     updateAgent === 'PM' || updateAgent === 'pm' ? '📋' : '⚙️';
        newActivities = appendActivities(state.activities, {
          id: nextId(),
          type: 'agent_text' as const,
          timestamp: event.timestamp,
          agent: updateAgent,
          content: `${icon} ${summaryText}`,
        });
        newLastAgentSummaries = { ...state.lastAgentSummaries, [updateAgent]: summaryText };
      }

      return {
        ...state,
        activities: newActivities,
        lastAgentSummaries: newLastAgentSummaries,
        agentStates: newAgentStates,
        liveAgentStream: newLiveStream,
        lastTicker: `${updateAgent}${remStr}: ${tickerAction}${progressStr}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_TOOL_USE': {
      const event = action.event;
      if (!event.agent) return state;

      const newActivities = isNewEvent(state, event)
        ? appendActivities(state.activities, {
            id: nextId(), type: 'tool_use' as const, timestamp: event.timestamp,
            agent: event.agent, tool_name: event.tool_name, tool_description: event.description,
          })
        : state.activities;

      return {
        ...state,
        activities: newActivities,
        agentStates: {
          ...state.agentStates,
          [event.agent]: {
            ...state.agentStates[event.agent],
            name: event.agent,
            current_tool: event.description,
            last_update_at: Date.now(),
          },
        },
        liveAgentStream: {
          ...state.liveAgentStream,
          [event.agent]: {
            ...state.liveAgentStream[event.agent],
            tool: event.tool_name,
            text: event.description || state.liveAgentStream[event.agent]?.text || '',
            timestamp: Date.now(),
          },
        },
        lastTicker: `${event.agent}: ${event.description || event.tool_name}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_AGENT_STARTED': {
      const event = action.event;
      if (!event.agent) return state;

      const newDagTaskStatus = event.task_id
        ? { ...state.dagTaskStatus, [event.task_id]: 'working' as const }
        : state.dagTaskStatus;

      const newActivities = isNewEvent(state, event)
        ? appendActivities(state.activities, {
            id: nextId(), type: 'agent_started' as const, timestamp: event.timestamp,
            agent: event.agent, task: event.task,
          })
        : state.activities;

      return {
        ...state,
        activities: newActivities,
        dagTaskStatus: newDagTaskStatus,
        agentStates: {
          ...state.agentStates,
          [event.agent]: {
            name: event.agent, state: 'working', task: event.task, current_tool: undefined,
            cost: state.agentStates[event.agent]?.cost ?? 0,
            turns: state.agentStates[event.agent]?.turns ?? 0,
            duration: state.agentStates[event.agent]?.duration ?? 0,
            last_result: undefined,
            started_at: Date.now(),
            last_update_at: Date.now(),
          },
        },
        sdkCalls: state.sdkCalls.some(c => c.agent === event.agent && c.status === 'running' && (!event.task_id || c.taskId === event.task_id))
          ? state.sdkCalls  // Already tracked from delegation event
          : [...state.sdkCalls, { agent: event.agent, startTime: event.timestamp, status: 'running', taskId: event.task_id, taskName: event.task_name || event.task?.slice(0, 120) }],
        liveAgentStream: {
          ...state.liveAgentStream,
          [event.agent]: {
            text: event.task?.slice(0, 200) || 'starting...',
            timestamp: Date.now(),
          },
        },
        lastTicker: `${event.agent} started${event.task ? ': ' + event.task.slice(0, 60) : ''}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_AGENT_FINISHED': {
      const event = action.event;
      if (!event.agent) return state;

      const newDagTaskStatus = event.task_id
        ? { ...state.dagTaskStatus, [event.task_id]: event.is_error ? 'failed' as const : 'completed' as const }
        : state.dagTaskStatus;

      const newDagTaskFailureReasons = (event.task_id && event.is_error && event.failure_reason)
        ? { ...state.dagTaskFailureReasons, [event.task_id]: event.failure_reason }
        : state.dagTaskFailureReasons;

      // Remove from live stream
      const newLiveStream = { ...state.liveAgentStream };
      delete newLiveStream[event.agent];

      const newActivities = isNewEvent(state, event)
        ? appendActivities(state.activities, {
            id: nextId(), type: 'agent_finished' as const, timestamp: event.timestamp,
            agent: event.agent, cost: event.cost, turns: event.turns,
            duration: event.duration, is_error: event.is_error,
            failure_reason: event.failure_reason,
          })
        : state.activities;

      // Update SDK calls — find the matching running entry for this agent.
      // Prefer matching by task_id (DAG mode) for accuracy when same role runs multiple tasks.
      const updatedSdkCalls = [...state.sdkCalls];
      let sdkIdx = -1;
      if (event.task_id) {
        // DAG mode: match by task_id first (most precise)
        for (let i = updatedSdkCalls.length - 1; i >= 0; i--) {
          if (updatedSdkCalls[i].taskId === event.task_id && updatedSdkCalls[i].status === 'running') {
            sdkIdx = i;
            break;
          }
        }
      }
      if (sdkIdx < 0) {
        // Fallback: match by agent name (legacy/non-DAG mode)
        for (let i = updatedSdkCalls.length - 1; i >= 0; i--) {
          if (updatedSdkCalls[i].agent === event.agent && updatedSdkCalls[i].status === 'running') {
            sdkIdx = i;
            break;
          }
        }
      }
      if (sdkIdx >= 0) {
        updatedSdkCalls[sdkIdx] = {
          ...updatedSdkCalls[sdkIdx],
          endTime: event.timestamp,
          cost: event.cost,
          status: event.is_error ? 'error' : 'done',
          turns: event.turns,
          failureReason: event.is_error ? event.failure_reason : undefined,
        };
      }

      return {
        ...state,
        activities: newActivities,
        dagTaskStatus: newDagTaskStatus,
        dagTaskFailureReasons: newDagTaskFailureReasons,
        liveAgentStream: newLiveStream,
        agentStates: {
          ...state.agentStates,
          [event.agent]: {
            ...state.agentStates[event.agent], name: event.agent,
            state: event.is_error ? 'error' : 'done', current_tool: undefined,
            cost: (state.agentStates[event.agent]?.cost ?? 0) + (event.cost ?? 0),
            turns: (state.agentStates[event.agent]?.turns ?? 0) + (event.turns ?? 0),
            duration: event.duration ?? 0,
            delegated_from: undefined, delegated_at: undefined,
            last_result: state.agentStates[event.agent]?.last_result,
            started_at: undefined,
            last_update_at: Date.now(),
          },
        },
        sdkCalls: updatedSdkCalls,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_DELEGATION': {
      const event = action.event;

      const newActivities = isNewEvent(state, event)
        ? appendActivities(state.activities, {
            id: nextId(), type: 'delegation' as const, timestamp: event.timestamp,
            from_agent: event.from_agent, to_agent: event.to_agent, task: event.task,
          })
        : state.activities;

      let newAgentStates = state.agentStates;
      if (event.to_agent) {
        newAgentStates = {
          ...newAgentStates,
          [event.to_agent]: {
            ...newAgentStates[event.to_agent],
            name: event.to_agent,
            state: 'working',
            task: event.task ?? newAgentStates[event.to_agent]?.task,
            delegated_from: event.from_agent,
            delegated_at: Date.now(),
            current_tool: undefined,
            started_at: Date.now(),
            last_update_at: Date.now(),
          },
        };
      }

      // Add an sdkCall entry so the Trace tab shows the sub-agent immediately
      // (WS_AGENT_STARTED may arrive later after SDK initialization delay)
      let newSdkCalls = state.sdkCalls;
      if (event.to_agent) {
        // Only add if there isn't already a running call for this agent
        const alreadyRunning = state.sdkCalls.some(
          c => c.agent === event.to_agent && c.status === 'running'
        );
        if (!alreadyRunning) {
          newSdkCalls = [...state.sdkCalls, {
            agent: event.to_agent, startTime: event.timestamp, status: 'running',
          }];
        }
      }

      return {
        ...state,
        activities: newActivities,
        agentStates: newAgentStates,
        sdkCalls: newSdkCalls,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_LOOP_PROGRESS': {
      const event = action.event;
      return {
        ...state,
        loopProgress: {
          loop: event.loop ?? 0, max_loops: event.max_loops ?? 0,
          turn: event.turn ?? 0, max_turns: event.max_turns ?? 0,
          cost: event.cost ?? 0, max_budget: event.max_budget ?? 0,
        },
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_AGENT_RESULT': {
      const event = action.event;
      if (!event.text) return state;

      // Prefer the explicit event.agent field; fall back to regex extraction from text.
      // Always lowercase to match agent state keys ("orchestrator" not "Orchestrator").
      const agentMatch = event.text.match(/\*(\w+)\*/);
      const resultAgent = (event.agent || (agentMatch ? agentMatch[1] : 'agent')).toLowerCase();

      let newAgentStates = state.agentStates;
      if (resultAgent && resultAgent !== 'agent' && state.agentStates[resultAgent]) {
        newAgentStates = {
          ...newAgentStates,
          [resultAgent]: {
            ...newAgentStates[resultAgent],
            last_result: event.text.slice(0, 200),
          },
        };
      }

      const newActivities = isNewEvent(state, event)
        ? appendActivities(state.activities, {
            id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
            agent: resultAgent, content: event.text,
          })
        : state.activities;

      return {
        ...state,
        activities: newActivities,
        agentStates: newAgentStates,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_AGENT_FINAL': {
      const event = action.event;

      const newActivities = event.text
        ? appendActivities(state.activities, {
            id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
            agent: 'system', content: event.text,
          })
        : state.activities;

      // Reset working/waiting agents to idle, preserve done/error
      const resetAgentStates: Record<string, AgentStateType> = {};
      for (const [k, v] of Object.entries(state.agentStates)) {
        resetAgentStates[k] = {
          ...v,
          state: (v.state === 'working' || v.state === 'waiting') ? 'idle' : v.state,
          current_tool: undefined,
        };
      }

      return {
        ...state,
        activities: newActivities,
        agentStates: resetAgentStates,
        loopProgress: null,
        lastTicker: '',
        liveAgentStream: {},
        lastAgentSummaries: {},
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_PROJECT_STATUS': {
      const event = action.event;

      if (event.status === 'running') {
        // New task — reset agent cards for clean slate
        const resetAgentStates: Record<string, AgentStateType> = {};
        for (const [k, v] of Object.entries(state.agentStates)) {
          resetAgentStates[k] = {
            ...v,
            state: 'idle',
            current_tool: undefined,
            task: undefined,
            last_result: undefined,
          };
        }
        return {
          ...state,
          agentStates: resetAgentStates,
          dagGraph: null,
          healingEvents: [],
          dagTaskStatus: {},
          dagTaskFailureReasons: {},
          liveAgentStream: {},
          lastAgentSummaries: {},
          lastSequenceId: trackSequence(state, event),
        };
      } else if (event.status === 'idle') {
        // Task ended — reset stale working/waiting states, preserve done/error
        const resetAgentStates: Record<string, AgentStateType> = {};
        for (const [k, v] of Object.entries(state.agentStates)) {
          resetAgentStates[k] = {
            ...v,
            state: (v.state === 'working' || v.state === 'waiting') ? 'idle' : v.state,
            current_tool: undefined,
          };
        }
        // Close all running sdkCalls so Trace doesn't show perpetual "running..."
        const closedSdkCalls = state.sdkCalls.map(c =>
          c.status === 'running'
            ? { ...c, status: 'done', endTime: event.timestamp }
            : c
        );
        return {
          ...state,
          agentStates: resetAgentStates,
          sdkCalls: closedSdkCalls,
          loopProgress: null,
          lastTicker: '',
          liveAgentStream: {},
          lastSequenceId: trackSequence(state, event),
        };
      }

      return { ...state, lastSequenceId: trackSequence(state, event) };
    }

    case 'WS_TASK_GRAPH': {
      const event = action.event;
      if (!event.graph) return state;

      return {
        ...state,
        dagGraph: event.graph,
        dagTaskStatus: {},
        dagTaskFailureReasons: {},
        // Auto-switch to Plan tab so user sees execution progress
        desktopTab: 'plan',
        mobileView: 'plan',
        activities: appendActivities(state.activities, {
          id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
          agent: 'PM',
          content: `📋 **DAG Plan:** ${event.graph.vision || 'Execution plan created'} (${event.graph.tasks?.length || 0} tasks)`,
        }),
        lastTicker: `Plan: ${event.graph.vision?.slice(0, 80) || 'DAG created'}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_DAG_TASK_UPDATE': {
      const event = action.event;
      const taskId = event.task_id;
      const status = event.status;
      if (!taskId || !status) return state;

      const mappedStatus =
        status === 'completed' ? 'completed' as const :
        status === 'working' ? 'working' as const :
        status === 'failed' ? 'failed' as const :
        status === 'cancelled' ? 'cancelled' as const :
        'pending' as const;

      // Capture failure reasons for failed/cancelled tasks
      const newFailureReasons = event.failure_reason
        ? { ...state.dagTaskFailureReasons, [taskId]: event.failure_reason as string }
        : state.dagTaskFailureReasons;

      return {
        ...state,
        dagTaskStatus: { ...state.dagTaskStatus, [taskId]: mappedStatus },
        dagTaskFailureReasons: newFailureReasons,
      };
    }

    case 'WS_EXECUTION_ERROR': {
      const event = action.event;
      const errorMessage = event.error_message || 'Unknown error';
      const errorType = event.error_type || 'unknown';
      const completedTasks = event.completed_tasks || 0;
      const totalTasks = event.total_tasks || 0;

      return {
        ...state,
        activities: appendActivities(state.activities, {
          id: nextId(),
          type: 'error' as const,
          timestamp: event.timestamp ?? Date.now() / 1000,
          agent: 'system',
          content: `\u274c **Execution ${errorType === 'cancelled' ? 'cancelled' : 'crashed'}:** ${errorMessage}\n\nCompleted ${completedTasks}/${totalTasks} tasks before failure.`,
        }),
        lastTicker: `\u274c Execution ${errorType === 'cancelled' ? 'cancelled' : 'failed'}: ${errorMessage.slice(0, 80)}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_SELF_HEALING': {
      const event = action.event;
      return {
        ...state,
        healingEvents: [...state.healingEvents, {
          timestamp: event.timestamp,
          failed_task: event.failed_task || '',
          failure_category: event.failure_category || 'unknown',
          remediation_task: event.remediation_task || '',
          remediation_role: event.remediation_role || '',
        }],
        activities: appendActivities(state.activities, {
          id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
          agent: 'system',
          content: `🔧 **Self-healing:** Task ${event.failed_task} failed (${event.failure_category}). Auto-fix: ${event.remediation_task} (${event.remediation_role})`,
        }),
        lastTicker: `🔧 Self-healing: ${event.failure_category} → ${event.remediation_role}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_APPROVAL_REQUEST': {
      const event = action.event;
      if (!event.description) return state;
      return { ...state, approvalRequest: event.description };
    }

    case 'WS_PRE_TASK_QUESTION':
      return { ...state, pendingQuestion: action.event.question ?? null };

    case 'CLEAR_PRE_TASK_QUESTION':
      return { ...state, pendingQuestion: null };

    case 'WS_HISTORY_CLEARED':
      return {
        ...state,
        activities: [],
        agentStates: {},
        loopProgress: null,
        lastTicker: '',
        sdkCalls: [],
        files: null,
        messageOffset: 0,
        dagGraph: null,
        dagTaskStatus: {},
        dagTaskFailureReasons: {},
        healingEvents: [],
        liveAgentStream: {},
        hasMoreMessages: false,
        approvalRequest: null,
        lastAgentSummaries: {},
        lastSequenceId: 0,
        sending: false,  // reset any stuck send state from a mid-flight clear
      };

    case 'WS_LIVE_STATE_SYNC': {
      const event = action.event;
      let newAgentStates = state.agentStates;
      let newLiveStream = state.liveAgentStream;
      let newLoopProgress = state.loopProgress;
      let newLastTicker = state.lastTicker;
      let newDagGraph = state.dagGraph;
      let newDagTaskStatus = state.dagTaskStatus;

      if (event.agent_states) {
        const restored: Record<string, AgentStateType> = {};
        const liveEntries: Record<string, LiveAgentEntry> = {};
        for (const [name, s] of Object.entries(event.agent_states)) {
          const isWorking = (s.state ?? 'idle') === 'working' || (s.state ?? 'idle') === 'waiting';
          restored[name] = {
            name,
            state: (s.state as AgentStateType['state']) ?? 'idle',
            task: s.task,
            current_tool: s.current_tool,
            cost: s.cost ?? 0,
            turns: s.turns ?? 0,
            duration: s.duration ?? 0,
            started_at: isWorking ? Date.now() : undefined,
            last_update_at: isWorking ? Date.now() : undefined,
          };
          if (isWorking) {
            liveEntries[name] = { text: s.task || 'working...', timestamp: Date.now() };
          }
        }
        newAgentStates = { ...newAgentStates, ...restored };
        if (Object.keys(liveEntries).length > 0) {
          newLiveStream = { ...newLiveStream, ...liveEntries };
        }
      }

      if (event.loop_progress) {
        newLoopProgress = event.loop_progress;
      }

      if (event.status === 'running') {
        newLastTicker = 'agents working...';
      }

      if (event.dag_graph) {
        newDagGraph = event.dag_graph;
      }

      if (event.dag_task_statuses && Object.keys(event.dag_task_statuses).length > 0) {
        newDagTaskStatus = {
          ...newDagTaskStatus,
          ...event.dag_task_statuses as Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>,
        };
      }

      return {
        ...state,
        agentStates: newAgentStates,
        liveAgentStream: newLiveStream,
        loopProgress: newLoopProgress,
        lastTicker: newLastTicker,
        dagGraph: newDagGraph,
        dagTaskStatus: newDagTaskStatus,
      };
    }

    case 'WS_TURN_PROGRESS': {
      // Update the live agent stream progress field with turn consumption info.
      // Shows e.g. "47/195 turns (24%)" in the live status panel.
      const { agent, turnsUsed, maxTurns, remaining } = action;
      const pct = Math.round((turnsUsed / Math.max(maxTurns, 1)) * 100);
      const progressStr = `${turnsUsed}/${maxTurns} turns · ${remaining} left (${pct}%)`;
      const existing = state.liveAgentStream[agent];
      if (!existing) return state; // Agent not currently tracked — ignore
      return {
        ...state,
        liveAgentStream: {
          ...state.liveAgentStream,
          [agent]: { ...existing, progress: progressStr },
        },
      };
    }

    // ────────────────────────── UI actions ──────────────────────────

    case 'SET_MOBILE_VIEW':
      return { ...state, mobileView: action.view };

    case 'SET_DESKTOP_TAB':
      return { ...state, desktopTab: action.tab };

    case 'SET_SELECTED_AGENT':
      return { ...state, selectedAgent: action.agent };

    case 'SET_SENDING':
      return { ...state, sending: action.sending };

    case 'SET_SHOW_CLEAR_CONFIRM':
      return { ...state, showClearConfirm: action.show };

    case 'ADD_ACTIVITY':
      return { ...state, activities: appendActivities(state.activities, action.activity) };

    case 'CLEAR_ALL_STATE':
      // Full state reset — clears everything including DAG graph, healing events,
      // live streams, and sequence tracking. Called when user clears chat history.
      return {
        ...state,
        activities: [],
        agentStates: {},
        loopProgress: null,
        lastTicker: '',
        sdkCalls: [],
        files: null,
        messageOffset: 0,
        hasMoreMessages: false,
        approvalRequest: null,
        lastAgentSummaries: {},
        dagGraph: null,
        dagTaskStatus: {},
        dagTaskFailureReasons: {},
        healingEvents: [],
        liveAgentStream: {},
        lastSequenceId: 0,
      };

    default:
      return state;
  }
}
