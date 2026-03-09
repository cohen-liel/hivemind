import { useEffect, useReducer, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { getProject, getMessages, getFiles, sendMessage, pauseProject, resumeProject, stopProject, getLiveState, clearHistory, getResumableTask, resumeInterruptedTask, discardInterruptedTask, getActivity } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { useIOSViewport } from '../useIOSViewport';
import { useToast } from '../components/Toast';
import ActivityFeed from '../components/ActivityFeed';
import AgentStatusPanel from '../components/AgentStatusPanel';
import AgentMetrics from '../components/AgentMetrics';
import { useAgentMetrics } from '../hooks/useAgentMetrics';
import ConductorBar from '../components/ConductorBar';
import FileDiff from '../components/FileDiff';
import PlanView from '../components/PlanView';
import NetworkTrace from '../components/NetworkTrace';
import ApprovalModal from '../components/ApprovalModal';
import Controls from '../components/Controls';
import CodeBrowser from '../components/CodeBrowser';
import ConductorMode from '../components/ConductorMode';
import type { ProjectMessage, WSEvent, ActivityEntry, AgentState as AgentStateType } from '../types';
import { SkeletonBlock } from '../components/Skeleton';
import type { ActivityEvent } from '../api';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';
import { projectReducer, initialProjectState } from '../reducers/projectReducer';
import type { MobileView, DesktopTab, SdkCall } from '../reducers/projectReducer';

function nextId(): string {
  return crypto.randomUUID();
}

function messagesToActivities(messages: ProjectMessage[]): ActivityEntry[] {
  return messages.map((msg) => ({
    id: `msg-${msg.timestamp}-${msg.agent_name}`,
    type: msg.agent_name === 'user' ? 'user_message' as const : 'agent_text' as const,
    timestamp: msg.timestamp,
    agent: msg.agent_name,
    content: msg.content,
    cost: msg.cost_usd,
  }));
}

/** Convert persisted activity events from DB into ActivityEntry objects for the feed. */
function activityEventsToEntries(events: ActivityEvent[]): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  for (const evt of events) {
    switch (evt.type) {
      case 'tool_use':
        entries.push({
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
          type: 'tool_use',
          timestamp: evt.timestamp,
          agent: evt.agent,
          tool_name: evt.tool_name,
          tool_description: evt.description,
        });
        break;
      case 'agent_started':
        entries.push({
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
          type: 'agent_started',
          timestamp: evt.timestamp,
          agent: evt.agent,
          task: evt.task,
        });
        break;
      case 'agent_finished':
        entries.push({
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
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
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
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
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
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
          id: `act-${evt.sequence_id ?? evt.timestamp}`,
          type: 'error',
          timestamp: evt.timestamp,
          agent: evt.agent,
          content: evt.text || evt.summary,
        });
        break;
      // Skip agent_update, agent_result, agent_final, project_status —
      // these are either ephemeral or duplicated in messages table
    }
  }
  return entries;
}

/** Reconstruct SdkCall entries from persisted agent_started/agent_finished events. */
function reconstructSdkCalls(events: ActivityEvent[]): SdkCall[] {
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
function reconstructAgentStates(events: ActivityEvent[]): Record<string, AgentStateType> {
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

export default function ProjectView(): React.ReactElement | null {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();

  // ── Single useReducer replaces 21 individual useState hooks ──
  const [state, dispatch] = useReducer(projectReducer, initialProjectState);
  const {
    project, activities, agentStates, loopProgress, files, loadError,
    sdkCalls, liveAgentStream, lastTicker, dagGraph, dagTaskStatus,
    healingEvents, mobileView, desktopTab, selectedAgent, showClearConfirm,
    sending, messageOffset, hasMoreMessages, approvalRequest, resumableTask,
  } = state;

  // Controlled input — stays as useState (high-frequency keystrokes)
  const [message, setMessage] = useState('');

  // Tick counter to force re-render for elapsed time displays (every second)
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Per-agent performance metrics — must be here (before early returns) to satisfy Rules of Hooks
  const agentMetrics = useAgentMetrics(activities);

  // Hydrate DAG state from localStorage on mount (survives page refresh)
  useEffect(() => {
    if (!id) return;
    try {
      const saved = localStorage.getItem(`nexus_dag_${id}`);
      if (saved) {
        const { graph, statuses, savedAt } = JSON.parse(saved);
        const AGE_LIMIT_MS = 24 * 60 * 60 * 1000; // 24 hours
        if (Date.now() - savedAt < AGE_LIMIT_MS && graph) {
          dispatch({ type: 'HYDRATE_DAG', graph, statuses: statuses ?? {} });
        }
      }
    } catch { /* corrupted storage — ignore */ }
  }, [id]);

  // Persist DAG state to localStorage whenever graph or task statuses change
  useEffect(() => {
    if (!id || !state.dagGraph) return;
    try {
      localStorage.setItem(`nexus_dag_${id}`, JSON.stringify({
        graph: state.dagGraph,
        statuses: state.dagTaskStatus,
        savedAt: Date.now(),
      }));
    } catch { /* quota exceeded — ignore */ }
  }, [id, state.dagGraph, state.dagTaskStatus]);

  const loadProject = useCallback(async (): Promise<void> => {
    if (!id) return;
    const p = await getProject(id);
    dispatch({ type: 'SET_PROJECT', project: p });

    // Restore pending approval from project data
    if (p.pending_approval) {
      dispatch({ type: 'SET_APPROVAL_REQUEST', request: p.pending_approval });
    }

    // Failsafe: merge agent_states from poll response to recover from missed WS events.
    if (p.agent_states && Object.keys(p.agent_states).length > 0) {
      dispatch({ type: 'MERGE_AGENT_STATES_FROM_POLL', agentStates: p.agent_states });
    }
  }, [id]);

  const loadFiles = useCallback(async (): Promise<void> => {
    if (!id) return;
    const f = await getFiles(id);
    dispatch({ type: 'SET_FILES', files: f });
  }, [id]);

  const loadEarlierMessages = useCallback(async (): Promise<void> => {
    if (!id || !hasMoreMessages) return;
    try {
      const data = await getMessages(id, 50, messageOffset);
      if (data.messages.length > 0) {
        const earlier = messagesToActivities(data.messages);
        dispatch({
          type: 'LOAD_EARLIER_MESSAGES',
          messages: earlier,
          newOffset: messageOffset + 50,
          hasMore: data.total > messageOffset + 50,
        });
      } else {
        dispatch({ type: 'LOAD_EARLIER_MESSAGES', messages: [], newOffset: messageOffset, hasMore: false });
      }
    } catch { /* ignore */ }
  }, [id, messageOffset, hasMoreMessages]);

  useEffect(() => {
    if (!id) return;
    dispatch({ type: 'SET_LOAD_ERROR', error: null });
    loadProject().catch((e) => dispatch({ type: 'SET_LOAD_ERROR', error: e.message || 'Failed to load project' }));

    // Load both messages AND activity events, merge into unified timeline
    Promise.all([
      getMessages(id, 100).catch(() => ({ messages: [] as ProjectMessage[], total: 0 })),
      getActivity(id, 0, 500).catch(() => ({ events: [] as ActivityEvent[], latest_sequence: 0, source: 'none' })),
    ]).then(([msgData, actData]) => {
      // Convert messages to activity entries (user_message + agent_text)
      const msgEntries = messagesToActivities(msgData.messages);
      // Convert persisted activity events to entries (tool_use, agent_started, etc.)
      const actEntries = activityEventsToEntries(actData.events);
      // Merge both, sort by timestamp for a unified timeline
      const merged = [...msgEntries, ...actEntries].sort((a, b) => a.timestamp - b.timestamp);

      // Reconstruct NetworkTrace SDK calls from agent_started/agent_finished events
      const restoredCalls = reconstructSdkCalls(actData.events);

      // Reconstruct agent states from activity events (done/error/working)
      const restoredStates = reconstructAgentStates(actData.events);

      // Single dispatch: load all initial data with sequence-based dedup tracking
      dispatch({
        type: 'LOAD_INITIAL_DATA',
        activities: merged,
        sdkCalls: restoredCalls,
        agentStates: restoredStates,
        hasMoreMessages: msgData.total > 100,
        messageOffset: 100,
        lastSequenceId: actData.latest_sequence ?? 0,
      });
    });

    loadFiles().catch(() => {});

    // Check for interrupted/resumable tasks (bug fix #6: only show if not currently running)
    Promise.all([getResumableTask(id), getProject(id)]).then(([data, proj]) => {
      if (data.resumable && data.task && proj.status !== 'running') {
        dispatch({ type: 'SET_RESUMABLE_TASK', task: data.task });
      } else {
        dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      }
    }).catch(() => {});

    // Full live state recovery — restores loop progress, agent states, approval on refresh
    getLiveState(id).then((live) => {
      if (live.agent_states && Object.keys(live.agent_states).length > 0) {
        const restored: Record<string, AgentStateType> = {};
        for (const [name, s] of Object.entries(live.agent_states)) {
          const isWorking = (s.state as AgentStateType['state']) === 'working';
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
        }
        dispatch({ type: 'MERGE_AGENT_STATES_FROM_LIVE', restored });
      }
      if (live.loop_progress) {
        dispatch({ type: 'RESTORE_LOOP_PROGRESS', progress: live.loop_progress });
      }
      if (live.pending_approval) {
        dispatch({ type: 'SET_APPROVAL_REQUEST', request: live.pending_approval });
      }
    }).catch(() => {});

    // Periodic status poll — catches missed WS events and stuck states
    const statusPoll = setInterval(() => {
      loadProject().catch(() => {});
    }, 10000); // every 10s
    return () => clearInterval(statusPoll);
  }, [id, loadProject, loadFiles]);

  const handleWSEvent = useCallback((event: WSEvent): void => {
    if (event.project_id !== id) return;

    switch (event.type) {
      case 'agent_update':
        dispatch({ type: 'WS_AGENT_UPDATE', event });
        break;

      case 'tool_use':
        dispatch({ type: 'WS_TOOL_USE', event });
        break;

      case 'agent_started':
        dispatch({ type: 'WS_AGENT_STARTED', event });
        break;

      case 'agent_finished':
        dispatch({ type: 'WS_AGENT_FINISHED', event });
        break;

      case 'delegation':
        dispatch({ type: 'WS_DELEGATION', event });
        break;

      case 'loop_progress':
        dispatch({ type: 'WS_LOOP_PROGRESS', event });
        break;

      case 'agent_result':
        dispatch({ type: 'WS_AGENT_RESULT', event });
        break;

      case 'agent_final':
        dispatch({ type: 'WS_AGENT_FINAL', event });
        // Side effects: reload project data and files
        loadProject().catch(() => {});
        loadFiles().catch(() => {});
        // Browser notification when task completes
        if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('Task Complete', {
            body: event.text?.slice(0, 100) || 'Agent finished working',
            icon: '/favicon.ico',
          });
        }
        break;

      case 'project_status':
        dispatch({ type: 'WS_PROJECT_STATUS', event });
        loadProject().catch(() => {});
        // Clear persisted DAG so the new plan starts fresh
        if (event.status === 'running' && id) {
          try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ }
        }
        break;

      case 'task_graph' as WSEvent['type']:
        dispatch({ type: 'WS_TASK_GRAPH', event });
        break;

      case 'self_healing' as WSEvent['type']:
        dispatch({ type: 'WS_SELF_HEALING', event });
        break;

      case 'approval_request' as WSEvent['type']:
        dispatch({ type: 'WS_APPROVAL_REQUEST', event });
        break;

      case 'history_cleared' as WSEvent['type']:
        dispatch({ type: 'WS_HISTORY_CLEARED' });
        if (id) { try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ } }
        loadProject().catch(() => {});
        break;

      case 'live_state_sync':
        dispatch({ type: 'WS_LIVE_STATE_SYNC', event });
        break;

      default:
        break;
    }
  }, [id, loadProject, loadFiles]);

  const { connected } = useWSSubscribe(handleWSEvent);
  useIOSViewport();

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
        <div className="text-center px-4 max-w-sm mx-auto animate-[fadeSlideIn_0.3s_ease-out]">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center text-2xl"
            style={{ background: 'var(--glow-red)', border: '1px solid rgba(245,71,91,0.2)' }}>
            ⚠️
          </div>
          <h3 className="text-sm font-bold mb-1" style={{ color: 'var(--accent-red)' }}>Failed to load project</h3>
          <p className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>{loadError}</p>
          <button
            onClick={() => {
              dispatch({ type: 'SET_LOAD_ERROR', error: null });
              loadProject().catch((e) => dispatch({ type: 'SET_LOAD_ERROR', error: e.message || 'Failed to load project' }));
            }}
            className="px-4 py-2 text-xs font-medium rounded-xl transition-all active:scale-95"
            style={{
              background: 'var(--glow-red)',
              color: 'var(--accent-red)',
              border: '1px solid rgba(245,71,91,0.2)',
            }}
          >
            ↻ Retry
          </button>
        </div>
      </div>
    );
  }

  if (!project || !id) {
    return (
      <div className="min-h-screen" style={{ background: 'var(--bg-void)' }}>
        {/* Conductor bar skeleton */}
        <div className="h-14" style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border-dim)' }}>
          <div className="flex items-center gap-3 px-4 h-full">
            <SkeletonBlock width="32px" height="32px" className="rounded-lg" />
            <SkeletonBlock width="140px" height="16px" />
            <div className="ml-auto flex items-center gap-2">
              <SkeletonBlock width="60px" height="24px" className="rounded-full" />
              <SkeletonBlock width="60px" height="24px" className="rounded-full" />
            </div>
          </div>
        </div>
        {/* Main content skeleton */}
        <div className="flex gap-4 p-4 animate-[fadeSlideIn_0.3s_ease-out]">
          {/* Left panel — 2/3 width, two placeholder cards */}
          <div className="flex-[2] space-y-4">
            <div className="rounded-2xl p-5 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
              <SkeletonBlock width="60%" height="14px" />
              <SkeletonBlock width="100%" height="80px" className="rounded-lg" />
              <SkeletonBlock width="40%" height="12px" />
            </div>
            <div className="rounded-2xl p-5 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
              <SkeletonBlock width="30%" height="14px" />
              <SkeletonBlock width="100%" height="120px" className="rounded-lg" />
            </div>
          </div>
          {/* Right sidebar — 1/3 width */}
          <div className="flex-1 hidden lg:block space-y-4">
            <div className="rounded-2xl p-4 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
              <SkeletonBlock width="50%" height="12px" />
              <div className="space-y-2">
                {[1, 2, 3].map(i => (
                  <div key={i} className="flex items-center gap-2">
                    <SkeletonBlock width="28px" height="28px" className="rounded-lg" />
                    <SkeletonBlock width="80px" height="10px" />
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-2xl p-4 space-y-2" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
              <SkeletonBlock width="40%" height="12px" />
              <SkeletonBlock width="100%" height="60px" className="rounded-lg" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  const handleSend = async (msg: string): Promise<void> => {
    // All messages go through the Orchestrator — no direct agent targeting
    dispatch({ type: 'ADD_ACTIVITY', activity: {
      id: nextId(), type: 'user_message', timestamp: Date.now() / 1000,
      agent: 'user', content: msg,
    }});
    try {
      await sendMessage(id, msg);
      toast.success('Message sent');
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to send message:', errMsg);
      toast.error('Send failed', errMsg);
      dispatch({ type: 'ADD_ACTIVITY', activity: {
        id: nextId(), type: 'error', timestamp: Date.now() / 1000,
        agent: 'system', content: `Failed to send: ${errMsg}`,
      }});
    }
    loadProject().catch(() => {});
  };

  const handlePause = async (): Promise<void> => {
    try {
      await pauseProject(id);
      toast.info('Project paused');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Pause failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleResume = async (): Promise<void> => {
    try {
      await resumeProject(id);
      toast.success('Project resumed');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Resume failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleStop = async (): Promise<void> => {
    try {
      await stopProject(id);
      toast.warning('Project stopped');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Stop failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleClearHistory = async (): Promise<void> => {
    dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false });
    try {
      await clearHistory(id);
      // Reset ALL frontend state — agent has no memory after clear
      dispatch({ type: 'CLEAR_ALL_STATE' });
      toast.success('History cleared');
      loadProject().catch(() => {});
    } catch (e) {
      console.error('Failed to clear history:', e);
      toast.error('Clear failed', e instanceof Error ? e.message : String(e));
    }
  };

  const agentStateList: AgentStateType[] = project.agents.map(name => (
    agentStates[name] ?? { name, state: 'idle', cost: 0, turns: 0, duration: 0 }
  ));

  const orchestratorState = agentStateList.find(a => a.name === 'orchestrator') ?? null;
  const subAgentStates = agentStateList.filter(a => a.name !== 'orchestrator');

  const mobileNavItems: { id: MobileView; icon: JSX.Element; label: string }[] = [
    {
      id: 'orchestra',
      label: 'Nexus',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>,
    },
    {
      id: 'activity',
      label: 'Log',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>,
    },
    {
      id: 'plan',
      label: 'Plan',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>,
    },
    {
      id: 'code',
      label: 'Code',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
    },
    {
      id: 'changes',
      label: 'Diff',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 3v18M3 12h18"/></svg>,
    },
    {
      id: 'trace',
      label: 'Trace',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,
    },
  ];

  const desktopTabItems: { id: DesktopTab; icon: JSX.Element; label: string }[] = [
    {
      id: 'nexus',
      label: 'Nexus',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>,
    },
    {
      id: 'agents',
      label: 'Agents',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>,
    },
    {
      id: 'plan',
      label: 'Plan',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>,
    },
    {
      id: 'code',
      label: 'Code',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
    },
    {
      id: 'diff',
      label: 'Diff',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 3v18M3 12h18"/></svg>,
    },
    {
      id: 'trace',
      label: 'Trace',
      icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,
    },
  ];

  return (
    <div className="h-full flex flex-col" style={{ background: 'var(--bg-void)', overflow: 'hidden', position: 'fixed', inset: 0 }}>

      {/* Resume interrupted task banner */}
      {resumableTask && (
        <div className="px-4 py-3 flex items-center justify-between gap-3 z-50" style={{
          background: 'linear-gradient(90deg, rgba(245,166,35,0.08), rgba(245,166,35,0.04))',
          borderBottom: '1px solid rgba(245,166,35,0.15)',
          animation: 'slideUp 0.3s ease-out',
        }}>
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{
              background: 'rgba(245,166,35,0.12)',
            }}>
              <span className="text-sm">⚠️</span>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium" style={{ color: 'var(--accent-amber)' }}>Interrupted Task Found</div>
              <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                {resumableTask.last_message.slice(0, 100)}
                {' — '}{resumableTask.current_loop} rounds, ${resumableTask.total_cost_usd.toFixed(4)}
              </div>
            </div>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              className="px-4 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
              style={{
                background: 'var(--accent-amber)',
                color: '#000',
                boxShadow: '0 2px 8px rgba(245,166,35,0.3)',
              }}
              onClick={async () => {
                if (!id) return;
                try {
                  await resumeInterruptedTask(id);
                  dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
                  toast.success('Task resumed');
                  loadProject();
                } catch (e: unknown) {
                  const msg = e instanceof Error ? e.message : 'Unknown error';
                  console.error('Resume failed:', msg);
                  toast.error('Failed to resume task', msg);
                }
              }}
            >
              Resume Task
            </button>
            <button
              className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-muted)',
                border: '1px solid var(--border-dim)',
              }}
              onClick={async () => {
                if (!id) return;
                try {
                  await discardInterruptedTask(id);
                  dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
                  toast.info('Task discarded');
                } catch (e: unknown) {
                  const msg = e instanceof Error ? e.message : 'Unknown error';
                  console.error('Discard failed:', msg);
                  toast.error('Failed to discard task', msg);
                }
              }}
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {/* ===== MOBILE LAYOUT ===== */}
      <div
        className="lg:hidden flex flex-col z-30"
        style={{
          position: 'fixed',
          top: 'var(--app-offset, 0px)',
          left: 0,
          right: 0,
          height: 'var(--app-height, 100vh)',
          background: 'var(--bg-void)',
          paddingTop: 'env(safe-area-inset-top, 0px)',
          overflow: 'hidden',
          touchAction: 'none',
        }}
      >

        {/* Conductor (top, compact) */}
        <ConductorBar
          projectName={project.project_name}
          status={project.status}
          connected={connected}
          orchestrator={orchestratorState}
          progress={loopProgress}
          totalCost={project.total_cost_usd}
          agentSummary={subAgentStates}
        />

        {/* Content (middle, flex-1 takes remaining space) */}
        <div className="flex-1 overflow-y-auto min-h-0" style={{ overscrollBehavior: 'none', touchAction: 'pan-y', WebkitOverflowScrolling: 'touch' }}>
          {mobileView === 'orchestra' && (
            <ConductorMode
              agents={agentStateList}
              progress={loopProgress}
              activities={activities}
              totalCost={project.total_cost_usd}
              status={project.status}
              messageDraft={message}
            />
          )}

          {mobileView === 'activity' && (
            <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={loadEarlierMessages} />
          )}

          {mobileView === 'code' && (
            <CodeBrowser projectId={id} />
          )}

          {mobileView === 'changes' && (
            <div className="p-3">
              <div className="rounded-xl p-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
                <FileDiff files={files} />
              </div>
            </div>
          )}
          {mobileView === 'plan' && (
            <PlanView activities={activities} dagGraph={dagGraph} dagTaskStatus={dagTaskStatus} />
          )}
          {mobileView === 'trace' && (
            <NetworkTrace calls={sdkCalls} />
          )}
        </div>

        {/* Bottom: ticker + tab nav + input */}
        <div className="flex-shrink-0"
          style={{ borderTop: '1px solid var(--border-dim)', background: 'var(--bg-panel)', backdropFilter: 'blur(12px)', touchAction: 'none' }}>
          {/* Live ticker */}
          {lastTicker && (
            <div className="px-3 pt-1.5 pb-0.5">
              <div className="text-[10px] truncate"
                style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)', opacity: 0.7 }}>
                {lastTicker}
              </div>
            </div>
          )}

          {/* Tab nav (icon-only, tight) */}
          <div className="flex items-center px-1">
            {mobileNavItems.map(item => (
              <button
                key={item.id}
                onClick={() => {
                  dispatch({ type: 'SET_MOBILE_VIEW', view: item.id });
                  // Haptic feedback on tab switch
                  if ('vibrate' in navigator) {
                    navigator.vibrate(8);
                  }
                }}
                className="flex-1 flex flex-col items-center justify-center py-1.5 transition-colors"
                style={{ color: mobileView === item.id ? 'var(--accent-blue)' : 'var(--text-muted)' }}
                aria-label={item.label}
                aria-current={mobileView === item.id ? 'page' : undefined}
              >
                {item.icon}
                <span className="text-[9px] mt-0.5">{item.label}</span>
                {/* Active tab indicator dot */}
                {mobileView === item.id && (
                  <div className="w-1 h-1 rounded-full mt-0.5"
                    style={{ background: 'var(--accent-blue)', boxShadow: '0 0 4px var(--glow-blue)' }} />
                )}
              </button>
            ))}

            {/* Inline action buttons */}
            {(project.status === 'running' || project.status === 'paused') && (
              <div className="flex items-center gap-0.5 pl-1 ml-1" style={{ borderLeft: '1px solid var(--border-dim)' }}>
                {project.status === 'running' && (
                  <button onClick={handlePause} className="p-1.5" style={{ color: 'var(--accent-amber)' }} aria-label="Pause project">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                      <rect x="4" y="3" width="3" height="10" rx="0.5"/>
                      <rect x="9" y="3" width="3" height="10" rx="0.5"/>
                    </svg>
                  </button>
                )}
                {project.status === 'paused' && (
                  <button onClick={handleResume} className="p-1.5" style={{ color: 'var(--accent-green)' }} aria-label="Resume project">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                      <path d="M4 3l9 5-9 5V3z"/>
                    </svg>
                  </button>
                )}
                <button onClick={handleStop} className="p-1.5" style={{ color: 'var(--accent-red)' }} aria-label="Stop project">
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                    <rect x="3" y="3" width="10" height="10" rx="1"/>
                  </svg>
                </button>
              </div>
            )}
            {/* Clear history button — visible when idle */}
            {project.status === 'idle' && activities.length > 0 && (
              <button onClick={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })} className="p-1.5 ml-1" style={{ color: 'var(--text-muted)' }}
                title="Clear history">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4"/>
                </svg>
              </button>
            )}
          </div>

          {/* Input row (compact) */}
          <div className="flex items-center gap-1.5 px-2 pt-1" style={{ paddingBottom: 'max(8px, env(safe-area-inset-bottom, 8px))' }}>
            <input
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  if (message.trim() && !sending) {
                    const msg = message.trim();
                    setMessage('');
                    dispatch({ type: 'SET_SENDING', sending: true });
                    handleSend(msg).finally(() => dispatch({ type: 'SET_SENDING', sending: false }));
                  }
                }
              }}
              disabled={sending}
              placeholder={project.status === 'idle' ? 'Send a task...' : 'Message...'}
              className="flex-1 text-base rounded-full px-4 py-2 focus:outline-none min-w-0 disabled:opacity-50 transition-colors"
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
              }}
            />
            <button
              onClick={() => {
                if (message.trim() && !sending) {
                  const msg = message.trim();
                  setMessage('');
                  dispatch({ type: 'SET_SENDING', sending: true });
                  handleSend(msg).finally(() => dispatch({ type: 'SET_SENDING', sending: false }));
                }
              }}
              disabled={!message.trim() || sending}
              className="p-2 rounded-full transition-all flex-shrink-0"
              style={{
                background: message.trim() && !sending ? 'var(--accent-blue)' : 'var(--bg-elevated)',
                color: message.trim() && !sending ? 'white' : 'var(--text-muted)',
                boxShadow: message.trim() && !sending ? '0 0 12px var(--glow-blue)' : 'none',
              }}
            >
              {sending ? (
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
                </svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M14 2L7 9M14 2l-5 12-2-5-5-2 12-5z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* ===== DESKTOP LAYOUT ===== */}
      <div className="hidden lg:flex flex-col h-full w-full overflow-hidden">
        {/* Conductor header with progress */}
        <ConductorBar
          projectName={project.project_name}
          status={project.status}
          connected={connected}
          orchestrator={orchestratorState}
          progress={loopProgress}
          totalCost={project.total_cost_usd}
          agentSummary={subAgentStates}
        />

        {/* Desktop tab bar */}
        <div className="flex-shrink-0 px-4 py-2" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)' }}>
          <div className="flex items-center gap-1">
            {desktopTabItems.map(tab => (
              <button
                key={tab.id}
                onClick={() => dispatch({ type: 'SET_DESKTOP_TAB', tab: tab.id })}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors"
                style={{
                  background: desktopTab === tab.id ? 'var(--bg-elevated)' : 'transparent',
                  color: desktopTab === tab.id ? 'var(--text-primary)' : 'var(--text-muted)',
                }}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
            {/* Clear history — desktop */}
            {project.status === 'idle' && activities.length > 0 && (
              <button onClick={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })} className="ml-auto p-1.5 rounded-lg transition-all hover:bg-[var(--bg-elevated)]"
                style={{ color: 'var(--text-muted)' }} title="Clear history">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4"/>
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* Live status strip — shows active agents and what they're doing */}
        {(() => {
          const workingAgents = subAgentStates.filter(a => a.state === 'working');
          const doneAgents = subAgentStates.filter(a => a.state === 'done');
          const errorAgents = subAgentStates.filter(a => a.state === 'error');
          // Also show orchestrator when it's actively working
          const orchestratorWorking = orchestratorState?.state === 'working' ? orchestratorState : null;
          const hasStatus = workingAgents.length > 0 || doneAgents.length > 0 || errorAgents.length > 0 || orchestratorWorking;
          if (!hasStatus) return null;

          return (
            <div className="flex-shrink-0 px-4 py-1.5 flex items-center gap-3 overflow-x-auto"
              style={{ borderBottom: '1px solid var(--border-dim)', background: 'linear-gradient(180deg, var(--bg-panel), var(--bg-void))' }}>
              {/* Orchestrator chip — shown when orchestrator itself is working (planning / thinking) */}
              {orchestratorWorking && (() => {
                const ac = getAgentAccent('orchestrator');
                const elapsedSec = orchestratorWorking.started_at ? Math.round((now - orchestratorWorking.started_at) / 1000) : 0;
                return (
                  <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg flex-shrink-0 animate-[fadeSlideIn_0.2s_ease-out]"
                    style={{ background: ac.bg, border: `1px solid ${ac.color}30` }}>
                    <div className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse" style={{ background: ac.color }} />
                    <span className="text-[11px] font-semibold" style={{ color: ac.color }}>
                      🎯 Orchestrator
                    </span>
                    {elapsedSec > 0 && (
                      <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
                        {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
                      </span>
                    )}
                    {orchestratorWorking.current_tool && (
                      <span className="text-[10px] leading-tight" style={{ color: `${ac.color}99`, fontFamily: 'var(--font-mono)', maxWidth: '200px', display: '-webkit-box', WebkitLineClamp: 1, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {orchestratorWorking.current_tool}
                      </span>
                    )}
                  </div>
                );
              })()}
              {workingAgents.map(agent => {
                const ac = getAgentAccent(agent.name);
                const elapsedSec = agent.started_at ? Math.round((now - agent.started_at) / 1000) : 0;
                const isStale = agent.last_update_at ? (now - agent.last_update_at) > 60000 : false;
                return (
                  <div key={agent.name} className="flex items-center gap-2 px-2.5 py-1 rounded-lg flex-shrink-0 animate-[fadeSlideIn_0.2s_ease-out]"
                    style={{ background: isStale ? 'rgba(245,166,35,0.06)' : ac.bg, border: `1px solid ${isStale ? 'rgba(245,166,35,0.25)' : ac.color + '25'}` }}>
                    <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isStale ? '' : 'animate-pulse'}`} style={{ background: isStale ? 'var(--accent-amber)' : ac.color }} />
                    <span className="text-[11px] font-semibold" style={{ color: isStale ? 'var(--accent-amber)' : ac.color }}>
                      {AGENT_ICONS[agent.name] || '\u{1F527}'} {AGENT_LABELS[agent.name] || agent.name}
                    </span>
                    {elapsedSec > 0 && (
                      <span className="text-[10px] font-mono" style={{ color: isStale ? 'var(--accent-amber)' : 'var(--text-muted)' }}>
                        {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
                      </span>
                    )}
                    {isStale && (
                      <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
                        STALE
                      </span>
                    )}
                    {agent.current_tool && !isStale && (
                      <span className="text-[10px] break-all leading-tight" style={{ color: `${ac.color}99`, fontFamily: 'var(--font-mono)', maxWidth: '300px', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {agent.current_tool}
                      </span>
                    )}
                  </div>
                );
              })}
              {doneAgents.length > 0 && (
                <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg flex-shrink-0"
                  style={{ background: 'rgba(61,214,140,0.04)', border: '1px solid rgba(61,214,140,0.12)' }}>
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent-green)' }} />
                  <span className="text-[10px] font-medium" style={{ color: 'var(--accent-green)' }}>
                    {doneAgents.length} done
                  </span>
                </div>
              )}
              {errorAgents.length > 0 && (
                <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg flex-shrink-0"
                  style={{ background: 'rgba(245,71,91,0.04)', border: '1px solid rgba(245,71,91,0.12)' }}>
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent-red)' }} />
                  <span className="text-[10px] font-medium" style={{ color: 'var(--accent-red)' }}>
                    {errorAgents.length} error
                  </span>
                </div>
              )}
              {lastTicker && (
                <span className="text-[10px] truncate ml-auto flex-shrink-0"
                  style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', maxWidth: '250px' }}>
                  {lastTicker}
                </span>
              )}
            </div>
          );
        })()}

        {/* Split view: tab content (left) + activity log (right) */}
        <div className="flex-1 flex min-h-0 overflow-hidden" style={{ width: '100%' }}>
          {/* Left panel: selected tab content */}
          <div className="overflow-y-auto overflow-x-hidden min-w-0" style={{ width: '65%', maxWidth: '65%', flexShrink: 0 }}>
            {desktopTab === 'nexus' && (
              <>
                <ConductorMode
                  agents={agentStateList}
                  progress={loopProgress}
                  activities={activities}
                  totalCost={project.total_cost_usd}
                  status={project.status}
                  messageDraft={message}
                />
                {/* DAG Visualization */}
                {dagGraph && dagGraph.tasks && dagGraph.tasks.length > 0 && (
                  <div className="px-6 pb-4">
                    <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
                      <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                        DAG Execution Plan
                      </h3>
                      <p className="text-sm mb-3" style={{ color: 'var(--text-secondary)' }}>{dagGraph.vision}</p>
                      <div className="space-y-2">
                        {dagGraph.tasks.map(task => {
                          const taskStatus = dagTaskStatus[task.id] ?? 'pending';
                          const stateColor = taskStatus === 'completed' ? 'var(--accent-green)'
                            : taskStatus === 'working' ? 'var(--accent-blue)'
                            : taskStatus === 'failed' ? 'var(--accent-red)'
                            : 'var(--text-muted)';
                          const stateIcon = taskStatus === 'completed' ? '✅'
                            : taskStatus === 'working' ? '🔄'
                            : taskStatus === 'failed' ? '❌'
                            : task.is_remediation ? '🔧' : '⏸️';
                          const agentEmoji = AGENT_ICONS[task.role] || '🤖';
                          const borderColor = taskStatus === 'working' ? 'rgba(0,149,255,0.35)'
                            : taskStatus === 'completed' ? 'rgba(61,214,140,0.25)'
                            : taskStatus === 'failed' ? 'rgba(245,71,91,0.25)'
                            : 'var(--border-dim)';
                          const bgColor = taskStatus === 'working' ? 'rgba(0,149,255,0.06)'
                            : taskStatus === 'completed' ? 'rgba(61,214,140,0.04)'
                            : 'var(--bg-elevated)';
                          return (
                            <div key={task.id} className="flex items-start gap-2 p-2.5 rounded-lg transition-all" style={{ background: bgColor, border: `1px solid ${borderColor}`, boxShadow: taskStatus === 'working' ? '0 0 10px rgba(0,149,255,0.06)' : 'none' }}>
                              <span className="text-sm flex-shrink-0 mt-0.5">{stateIcon}</span>
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span className="text-xs font-mono font-semibold" style={{ color: stateColor }}>{agentEmoji} {task.role}</span>
                                  <span className="text-[10px] font-mono opacity-50" style={{ color: 'var(--text-muted)' }}>{task.id}</span>
                                  {task.is_remediation && <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: 'var(--glow-amber)', color: 'var(--accent-amber)' }}>fix</span>}
                                  {task.depends_on && task.depends_on.length > 0 && (
                                    <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                                      ← {task.depends_on.join(', ')}
                                    </span>
                                  )}
                                  {taskStatus === 'working' && (
                                    <span className="text-[10px] animate-pulse font-medium" style={{ color: 'var(--accent-blue)' }}>running...</span>
                                  )}
                                </div>
                                <p className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{task.goal}</p>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                )}
                {/* Self-Healing Events */}
                {healingEvents.length > 0 && (
                  <div className="px-6 pb-4">
                    <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid rgba(245,158,11,0.2)' }}>
                      <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
                        🔧 Self-Healing ({healingEvents.length})
                      </h3>
                      <div className="space-y-2">
                        {healingEvents.map((h, i) => (
                          <div key={i} className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
                            <span className="px-1.5 py-0.5 rounded" style={{ background: 'var(--glow-red)', color: 'var(--accent-red)', fontSize: '10px' }}>{h.failure_category}</span>
                            <span>{h.failed_task}</span>
                            <span style={{ color: 'var(--text-muted)' }}>→</span>
                            <span className="font-mono" style={{ color: 'var(--accent-green)' }}>{h.remediation_role}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
            {desktopTab === 'agents' && (
              <div className="p-6 space-y-6">
                <AgentStatusPanel
                  agents={agentStateList}
                  onSelectAgent={(agent) => dispatch({ type: 'SET_SELECTED_AGENT', agent })}
                  selectedAgent={selectedAgent}
                  layout="grid"
                />
                {agentMetrics.length > 0 && (
                  <AgentMetrics metrics={agentMetrics} />
                )}
              </div>
            )}
            {desktopTab === 'plan' && (
              <PlanView activities={activities} dagGraph={dagGraph} dagTaskStatus={dagTaskStatus} />
            )}
            {desktopTab === 'code' && (
              <CodeBrowser projectId={id} />
            )}
            {desktopTab === 'diff' && (
              <div className="p-6">
                <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
                  <FileDiff files={files} />
                </div>
              </div>
            )}
            {desktopTab === 'trace' && (
              <div className="p-6">
                <NetworkTrace calls={sdkCalls} />
              </div>
            )}
          </div>

          {/* Right panel: permanent activity log + chat input */}
          <div className="flex flex-col min-w-0 overflow-hidden" style={{ width: '35%', maxWidth: '35%', flexShrink: 0, borderLeft: '1px solid var(--border-dim)', background: 'var(--bg-panel)' }}>
            {/* Header */}
            <div className="px-4 py-2 flex items-center justify-between flex-shrink-0" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)', zIndex: 10 }}>
              <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Activity Log</h3>
              {Object.values(agentStates).filter(a => a.state === 'working').length > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
                  <span className="text-[10px] font-mono" style={{ color: 'var(--accent-green)' }}>
                    {Object.values(agentStates).filter(a => a.state === 'working').length} running
                  </span>
                </div>
              )}
            </div>

            {/* Live Agent Stream — sticky section showing what EVERY working agent is doing NOW */}
            {(() => {
              // Use agentStates as source of truth: ALL working agents, with liveAgentStream data overlaid
              const activeAgents = Object.entries(agentStates)
                .filter(([_, a]) => a.state === 'working')
                .map(([name, agentState]) => ({
                  name,
                  entry: liveAgentStream[name] ?? {
                    text: agentState.task || 'working...',
                    timestamp: agentState.started_at ?? now,
                  },
                  agentState,
                }));
              if (activeAgents.length === 0) return null;
              return (
                <div className="flex-shrink-0 overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-elevated)', maxHeight: '240px', overflowY: 'auto' }}>
                  <div className="px-3 pt-2 pb-1 flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full animate-pulse flex-shrink-0" style={{ background: 'var(--accent-green)' }} />
                    <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>
                      ⚡ Live — {activeAgents.length} agent{activeAgents.length > 1 ? 's' : ''} working
                    </span>
                  </div>
                  {activeAgents.map(({ name: agentName, entry, agentState }) => {
                    const ac = getAgentAccent(agentName);
                    const elapsedSec = agentState.started_at ? Math.round((now - agentState.started_at) / 1000) : 0;
                    return (
                      <div key={agentName} className="px-3 pb-2.5 pt-1" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        {/* Agent name row */}
                        <div className="flex items-center gap-2 mb-1">
                          <div className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse" style={{ background: ac.color }} />
                          <span className="text-[11px] font-semibold" style={{ color: ac.color }}>
                            {AGENT_ICONS[agentName] || '🤖'} {AGENT_LABELS[agentName] || agentName}
                          </span>
                          {entry.tool && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded font-mono font-medium flex-shrink-0" style={{ background: `${ac.color}18`, color: ac.color, border: `1px solid ${ac.color}30` }}>
                              {entry.tool}
                            </span>
                          )}
                          {elapsedSec > 0 && (
                            <span className="text-[10px] ml-auto font-mono flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
                              {elapsedSec >= 60 ? `${Math.floor(elapsedSec/60)}m${elapsedSec%60}s` : `${elapsedSec}s`}
                            </span>
                          )}
                        </div>
                        {/* Current thought / action */}
                        {entry.text && (
                          <p className="text-[11px] leading-relaxed pl-3.5" style={{
                            color: 'var(--text-secondary)',
                            fontFamily: 'var(--font-mono)',
                            wordBreak: 'break-word',
                            display: '-webkit-box',
                            WebkitLineClamp: 3,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                          }}>
                            {entry.text}
                          </p>
                        )}
                        {entry.progress && (
                          <span className="text-[10px] pl-3.5 mt-0.5 block font-mono" style={{ color: 'var(--text-muted)' }}>
                            {entry.progress}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })()}

            <div className="flex-1 overflow-y-auto min-h-0">
              <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={loadEarlierMessages} />
            </div>
            {/* Chat input — anchored to bottom of activity panel */}
            <Controls
              status={project.status}
              onPause={handlePause}
              onResume={handleResume}
              onStop={handleStop}
              onSend={handleSend}
            />
          </div>
        </div>
      </div>

      {/* Approval Modal */}
      {approvalRequest && id && (
        <ApprovalModal
          description={approvalRequest}
          projectId={id}
          onClose={() => dispatch({ type: 'SET_APPROVAL_REQUEST', request: null })}
        />
      )}

      {/* Clear History Confirmation Modal */}
      {showClearConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]"
          style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
          onClick={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false })}
        >
          <div
            className="rounded-2xl w-full max-w-sm mx-4 overflow-hidden"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-dim)',
              boxShadow: '0 25px 50px rgba(0,0,0,0.4)',
            }}
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-labelledby="clear-confirm-title"
          >
            {/* Red accent stripe */}
            <div className="h-1 w-full" style={{ background: 'linear-gradient(90deg, var(--accent-red), var(--accent-amber))' }} />
            <div className="p-5">
              <div className="flex items-start gap-3 mb-4">
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center text-lg flex-shrink-0"
                  style={{ background: 'var(--glow-red)' }}
                >
                  🗑️
                </div>
                <div>
                  <h3
                    id="clear-confirm-title"
                    className="text-base font-bold"
                    style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
                  >
                    Clear History?
                  </h3>
                  <p className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                    This will permanently delete all conversation history, agent states, and activity logs for this project. The agent will start fresh with no memory.
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false })}
                  className="px-4 py-2 text-sm font-medium rounded-xl transition-all"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-dim)' }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleClearHistory}
                  className="px-4 py-2 text-sm font-semibold rounded-xl transition-all text-white active:scale-[0.97]"
                  style={{
                    background: 'var(--accent-red)',
                    boxShadow: '0 2px 10px var(--glow-red)',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 4px 20px rgba(245,71,91,0.4)'; }}
                  onMouseLeave={e => { e.currentTarget.style.boxShadow = '0 2px 10px var(--glow-red)'; }}
                >
                  Clear All History
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
