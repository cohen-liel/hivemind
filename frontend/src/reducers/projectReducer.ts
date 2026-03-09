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
// Supporting Types
// ============================================================================

export interface SdkCall {
  agent: string;
  startTime: number;
  endTime?: number;
  cost?: number;
  status: string;
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

export interface ResumableTask {
  last_message: string;
  current_loop: number;
  total_cost_usd: number;
}

export type MobileView = 'orchestra' | 'activity' | 'code' | 'changes' | 'plan' | 'trace';
export type DesktopTab = 'nexus' | 'agents' | 'plan' | 'code' | 'diff' | 'trace';

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
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed'>;
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
  resumableTask: ResumableTask | null;

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
  healingEvents: [],
  mobileView: 'orchestra',
  desktopTab: 'nexus',
  selectedAgent: null,
  showClearConfirm: false,
  sending: false,
  messageOffset: 0,
  hasMoreMessages: false,
  approvalRequest: null,
  resumableTask: null,
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
  | { type: 'SET_RESUMABLE_TASK'; task: ResumableTask | null }
  | { type: 'SET_APPROVAL_REQUEST'; request: string | null }
  | {
      type: 'LOAD_INITIAL_DATA';
      activities: ActivityEntry[];
      sdkCalls: SdkCall[];
      agentStates: Record<string, AgentStateType>;
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
      statuses: Record<string, 'pending' | 'working' | 'completed' | 'failed'>;
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
  | { type: 'WS_SELF_HEALING'; event: WSEvent }
  | { type: 'WS_APPROVAL_REQUEST'; event: WSEvent }
  | { type: 'WS_HISTORY_CLEARED' }
  | { type: 'WS_LIVE_STATE_SYNC'; event: WSEvent }

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
  return crypto.randomUUID();
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

    case 'SET_RESUMABLE_TASK':
      return { ...state, resumableTask: action.task };

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
      return {
        ...state,
        activities: action.activities,
        sdkCalls: action.sdkCalls.length > 0 ? action.sdkCalls : state.sdkCalls,
        agentStates: mergedAgentStates,
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
      let changed = false;
      const updated = { ...state.agentStates };
      for (const [name, s] of Object.entries(action.agentStates)) {
        const serverState = (s.state ?? 'idle') as AgentStateType['state'];
        const ourState = updated[name]?.state ?? 'idle';
        const shouldSync =
          serverState === 'working'
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
            started_at: updated[name]?.started_at ?? (serverState === 'working' ? Date.now() : undefined),
            last_update_at: serverState === 'working' ? Date.now() : updated[name]?.last_update_at,
          };
          changed = true;
        }
      }
      return changed ? { ...state, agentStates: updated } : state;
    }

    case 'MERGE_AGENT_STATES_FROM_LIVE': {
      const hasLiveData = Object.values(state.agentStates).some(a => a.state === 'working');
      if (hasLiveData) return state;
      return { ...state, agentStates: { ...state.agentStates, ...action.restored } };
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
        newActivities = [...state.activities, {
          id: nextId(),
          type: 'agent_text' as const,
          timestamp: event.timestamp,
          agent: updateAgent,
          content: `${icon} ${summaryText}`,
        }];
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
        ? [...state.activities, {
            id: nextId(), type: 'tool_use' as const, timestamp: event.timestamp,
            agent: event.agent, tool_name: event.tool_name, tool_description: event.description,
          }]
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
        ? [...state.activities, {
            id: nextId(), type: 'agent_started' as const, timestamp: event.timestamp,
            agent: event.agent, task: event.task,
          }]
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
        sdkCalls: [...state.sdkCalls, {
          agent: event.agent, startTime: event.timestamp, status: 'running',
        }],
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

      // Remove from live stream
      const newLiveStream = { ...state.liveAgentStream };
      delete newLiveStream[event.agent];

      const newActivities = isNewEvent(state, event)
        ? [...state.activities, {
            id: nextId(), type: 'agent_finished' as const, timestamp: event.timestamp,
            agent: event.agent, cost: event.cost, turns: event.turns,
            duration: event.duration, is_error: event.is_error,
            failure_reason: event.failure_reason,
          }]
        : state.activities;

      // Update SDK calls — find last running entry for this agent
      const updatedSdkCalls = [...state.sdkCalls];
      let sdkIdx = -1;
      for (let i = updatedSdkCalls.length - 1; i >= 0; i--) {
        if (updatedSdkCalls[i].agent === event.agent && updatedSdkCalls[i].status === 'running') {
          sdkIdx = i;
          break;
        }
      }
      if (sdkIdx >= 0) {
        updatedSdkCalls[sdkIdx] = {
          ...updatedSdkCalls[sdkIdx],
          endTime: event.timestamp,
          cost: event.cost,
          status: event.is_error ? 'error' : 'done',
        };
      }

      return {
        ...state,
        activities: newActivities,
        dagTaskStatus: newDagTaskStatus,
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
        ? [...state.activities, {
            id: nextId(), type: 'delegation' as const, timestamp: event.timestamp,
            from_agent: event.from_agent, to_agent: event.to_agent, task: event.task,
          }]
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

      return {
        ...state,
        activities: newActivities,
        agentStates: newAgentStates,
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

      const agentMatch = event.text.match(/\*(\w+)\*/);
      const resultAgent = agentMatch ? agentMatch[1] : (event.agent || 'agent');

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

      return {
        ...state,
        activities: [...state.activities, {
          id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
          agent: resultAgent, content: event.text,
        }],
        agentStates: newAgentStates,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_AGENT_FINAL': {
      const event = action.event;

      const newActivities = event.text
        ? [...state.activities, {
            id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
            agent: 'system', content: event.text,
          }]
        : state.activities;

      // Reset working agents to idle, preserve done/error
      const resetAgentStates: Record<string, AgentStateType> = {};
      for (const [k, v] of Object.entries(state.agentStates)) {
        resetAgentStates[k] = {
          ...v,
          state: v.state === 'working' ? 'idle' : v.state,
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
          liveAgentStream: {},
          lastAgentSummaries: {},
          lastSequenceId: trackSequence(state, event),
        };
      } else if (event.status === 'idle') {
        // Task ended — reset stale working states, preserve done/error
        const resetAgentStates: Record<string, AgentStateType> = {};
        for (const [k, v] of Object.entries(state.agentStates)) {
          resetAgentStates[k] = {
            ...v,
            state: v.state === 'working' ? 'idle' : v.state,
            current_tool: undefined,
          };
        }
        return {
          ...state,
          agentStates: resetAgentStates,
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
        activities: [...state.activities, {
          id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
          agent: 'PM',
          content: `📋 **DAG Plan:** ${event.graph.vision || 'Execution plan created'} (${event.graph.tasks?.length || 0} tasks)`,
        }],
        lastTicker: `Plan: ${event.graph.vision?.slice(0, 80) || 'DAG created'}`,
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
        activities: [...state.activities, {
          id: nextId(), type: 'agent_text' as const, timestamp: event.timestamp,
          agent: 'system',
          content: `🔧 **Self-healing:** Task ${event.failed_task} failed (${event.failure_category}). Auto-fix: ${event.remediation_task} (${event.remediation_role})`,
        }],
        lastTicker: `🔧 Self-healing: ${event.failure_category} → ${event.remediation_role}`,
        lastSequenceId: trackSequence(state, event),
      };
    }

    case 'WS_APPROVAL_REQUEST': {
      const event = action.event;
      if (!event.description) return state;
      return { ...state, approvalRequest: event.description };
    }

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
        healingEvents: [],
        liveAgentStream: {},
        hasMoreMessages: false,
        approvalRequest: null,
        lastAgentSummaries: {},
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
          const isWorking = (s.state ?? 'idle') === 'working';
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
          ...event.dag_task_statuses as Record<string, 'pending' | 'working' | 'completed' | 'failed'>,
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
      return { ...state, activities: [...state.activities, action.activity] };

    case 'CLEAR_ALL_STATE':
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
      };

    default:
      return state;
  }
}
