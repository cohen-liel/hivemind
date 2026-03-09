import { useEffect, useState, useCallback, useRef } from 'react';
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
import type { Project, ProjectMessage, FileChanges, WSEvent, ActivityEntry, AgentState as AgentStateType, LoopProgress } from '../types';
import { SkeletonBlock } from '../components/Skeleton';
import type { ActivityEvent } from '../api';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

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
function reconstructSdkCalls(events: ActivityEvent[]): Array<{
  agent: string; startTime: number; endTime?: number; cost?: number; status: string;
}> {
  const calls: Array<{
    agent: string; startTime: number; endTime?: number; cost?: number; status: string;
  }> = [];
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

type MobileView = 'orchestra' | 'activity' | 'code' | 'changes' | 'plan' | 'trace';
type DesktopTab = 'nexus' | 'agents' | 'plan' | 'code' | 'diff' | 'trace';

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [agentStates, setAgentStates] = useState<Record<string, AgentStateType>>({});
  const [loopProgress, setLoopProgress] = useState<LoopProgress | null>(null);
  const [files, setFiles] = useState<FileChanges | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>('orchestra');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [lastTicker, setLastTicker] = useState('');
  const [sending, setSending] = useState(false);
  const [desktopTab, setDesktopTab] = useState<DesktopTab>('nexus');
  const [sdkCalls, setSdkCalls] = useState<Array<{
    agent: string; startTime: number; endTime?: number; cost?: number; status: string;
  }>>([]);
  const [messageOffset, setMessageOffset] = useState(0);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [approvalRequest, setApprovalRequest] = useState<string | null>(null);
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [resumableTask, setResumableTask] = useState<{ last_message: string; current_loop: number; total_cost_usd: number } | null>(null);
  const [dagGraph, setDagGraph] = useState<WSEvent['graph'] | null>(null);
  const [dagTaskStatus, setDagTaskStatus] = useState<Record<string, 'pending' | 'working' | 'completed' | 'failed'>>({});
  const [healingEvents, setHealingEvents] = useState<Array<{
    timestamp: number; failed_task: string; failure_category: string;
    remediation_task: string; remediation_role: string;
  }>>([]);
  // Live agent stream: what each agent is doing RIGHT NOW (thoughts, tool, progress)
  const [liveAgentStream, setLiveAgentStream] = useState<Record<string, {
    text: string; tool?: string; timestamp: number; progress?: string;
  }>>({});

  // Track the latest activity timestamp loaded from DB to prevent duplicate WS events
  const activityLoadedUpToRef = useRef<number>(0);

  // Tick counter to force re-render for elapsed time displays (every 5s)
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick(t => t + 1), 5000);
    return () => clearInterval(timer);
  }, []);

  // Hydrate DAG state from localStorage on mount (survives page refresh)
  useEffect(() => {
    if (!id) return;
    try {
      const saved = localStorage.getItem(`nexus_dag_${id}`);
      if (saved) {
        const { graph, statuses, savedAt } = JSON.parse(saved);
        const AGE_LIMIT_MS = 24 * 60 * 60 * 1000; // 24 hours
        if (Date.now() - savedAt < AGE_LIMIT_MS && graph) {
          setDagGraph(graph);
          setDagTaskStatus(statuses ?? {});
        }
      }
    } catch { /* corrupted storage — ignore */ }
  }, [id]);

  // Persist DAG state to localStorage whenever graph or task statuses change
  useEffect(() => {
    if (!id || !dagGraph) return;
    try {
      localStorage.setItem(`nexus_dag_${id}`, JSON.stringify({
        graph: dagGraph,
        statuses: dagTaskStatus,
        savedAt: Date.now(),
      }));
    } catch { /* quota exceeded — ignore */ }
  }, [id, dagGraph, dagTaskStatus]);

  const loadProject = useCallback(async () => {
    if (!id) return;
    const p = await getProject(id);
    setProject(p);

    // Restore pending approval from project data
    if (p.pending_approval) {
      setApprovalRequest(p.pending_approval);
    }

    // Failsafe: merge agent_states from poll response to recover from missed WS events.
    // Always sync working agents' current_tool; upgrade idle→working but don't downgrade working→idle.
    if (p.agent_states && Object.keys(p.agent_states).length > 0) {
      setAgentStates(prev => {
        let changed = false;
        const updated = { ...prev };
        for (const [name, s] of Object.entries(p.agent_states!)) {
          const serverState = (s.state ?? 'idle') as AgentStateType['state'];
          const ourState = updated[name]?.state ?? 'idle';
          // Always sync if server says working, or if both agree on state but tool changed
          const shouldSync = serverState === 'working'
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
              // Preserve timing if we had it, otherwise set for working agents
              started_at: updated[name]?.started_at ?? (serverState === 'working' ? Date.now() : undefined),
              last_update_at: serverState === 'working' ? Date.now() : updated[name]?.last_update_at,
            };
            changed = true;
          }
        }
        return changed ? updated : prev;
      });
    }
  }, [id]);

  const loadFiles = useCallback(async () => {
    if (!id) return;
    const f = await getFiles(id);
    setFiles(f);
  }, [id]);

  const loadEarlierMessages = useCallback(async () => {
    if (!id || !hasMoreMessages) return;
    try {
      const data = await getMessages(id, 50, messageOffset);
      if (data.messages.length > 0) {
        const earlier = messagesToActivities(data.messages);
        setActivities(prev => [...earlier, ...prev]);
        setMessageOffset(prev => prev + 50);
        setHasMoreMessages(data.total > messageOffset + 50);
      } else {
        setHasMoreMessages(false);
      }
    } catch { /* ignore */ }
  }, [id, messageOffset, hasMoreMessages]);

  useEffect(() => {
    if (!id) return;
    setLoadError(null);
    loadProject().catch((e) => setLoadError(e.message || 'Failed to load project'));

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
      setActivities(merged);
      setHasMoreMessages(msgData.total > 100);
      setMessageOffset(100);

      // Track the latest loaded timestamp so WS events don't duplicate DB entries
      const maxTs = merged.reduce((max, e) => Math.max(max, e.timestamp), 0);
      activityLoadedUpToRef.current = maxTs;

      // Reconstruct NetworkTrace SDK calls from agent_started/agent_finished events
      const restoredCalls = reconstructSdkCalls(actData.events);
      if (restoredCalls.length > 0) {
        setSdkCalls(restoredCalls);
      }

      // Reconstruct agent states from activity events (done/error/working)
      const restoredStates = reconstructAgentStates(actData.events);
      if (Object.keys(restoredStates).length > 0) {
        setAgentStates(prev => {
          // Only apply restored states if we don't have live state already
          const merged = { ...prev };
          for (const [name, state] of Object.entries(restoredStates)) {
            if (!merged[name] || merged[name].state === 'idle') {
              merged[name] = state;
            }
          }
          return merged;
        });
      }
    });

    loadFiles().catch(() => {});

    // Check for interrupted/resumable tasks (bug fix #6: only show if not currently running)
    Promise.all([getResumableTask(id), getProject(id)]).then(([data, proj]) => {
      if (data.resumable && data.task && proj.status !== 'running') {
        setResumableTask(data.task);
      } else {
        setResumableTask(null);
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
        setAgentStates(prev => {
          // Only restore from live state if we don't already have fresher WS data
          const hasLiveData = Object.values(prev).some(a => a.state === 'working');
          return hasLiveData ? prev : { ...prev, ...restored };
        });
      }
      if (live.loop_progress) {
        setLoopProgress(live.loop_progress);
      }
      if (live.pending_approval) {
        setApprovalRequest(live.pending_approval);
      }
    }).catch(() => {});

    // Periodic status poll — catches missed WS events and stuck states
    const statusPoll = setInterval(() => {
      loadProject().catch(() => {});
    }, 10000); // every 10s
    return () => clearInterval(statusPoll);
  }, [id, loadProject, loadFiles]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (event.project_id !== id) return;

    switch (event.type) {
      case 'agent_update': {
        // Live progress from agents — show what each agent is doing RIGHT NOW
        const updateAgent = event.agent || (event.text?.match(/\*(\w+)\*/)?.[1]);
        if (updateAgent) {
          const agentStatus = event.status === 'error' ? 'error' as const
            : event.status === 'done' ? 'done' as const : 'working' as const;
          setAgentStates(prev => ({
            ...prev,
            [updateAgent]: {
              ...prev[updateAgent],
              name: updateAgent,
              state: agentStatus,
              current_tool: event.summary || event.text?.slice(0, 150),
              cost: event.cost ?? prev[updateAgent]?.cost ?? 0,
              last_update_at: Date.now(),
              started_at: prev[updateAgent]?.started_at ?? (agentStatus === 'working' ? Date.now() : undefined),
            },
          }));
          // Update live agent stream with current thought/action
          const liveText = event.summary || event.text || '';
          if (liveText && agentStatus === 'working') {
            setLiveAgentStream(prev => ({
              ...prev,
              [updateAgent]: {
                text: liveText.slice(0, 300),
                tool: prev[updateAgent]?.tool,
                timestamp: Date.now(),
                progress: event.progress,
              },
            }));
          }
          // Show agent name + action + progress in ticker
          const progressStr = event.progress ? ` (${event.progress})` : '';
          const remStr = event.is_remediation ? ' 🔧' : '';
          const action = event.summary || event.text?.slice(0, 100) || 'working...';
          setLastTicker(`${updateAgent}${remStr}: ${action}${progressStr}`);
          // BUG FIX: clean up liveAgentStream when agent transitions to error/done via agent_update
          if (agentStatus !== 'working') {
            setLiveAgentStream(prev => {
              const next = { ...prev };
              delete next[updateAgent];
              return next;
            });
          }
        }
        break;
      }

      case 'tool_use':
        if (!event.agent) break;
        if (event.timestamp > activityLoadedUpToRef.current) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'tool_use', timestamp: event.timestamp,
            agent: event.agent, tool_name: event.tool_name, tool_description: event.description,
          }]);
        }
        setAgentStates(prev => ({
          ...prev,
          [event.agent!]: { ...prev[event.agent!], name: event.agent!, current_tool: event.description, last_update_at: Date.now() },
        }));
        // Update live stream: show the tool being used with its description
        setLiveAgentStream(prev => ({
          ...prev,
          [event.agent!]: {
            ...prev[event.agent!],
            tool: event.tool_name,
            text: event.description || prev[event.agent!]?.text || '',
            timestamp: Date.now(),
          },
        }));
        setLastTicker(`${event.agent}: ${event.description || event.tool_name}`);
        break;

      case 'agent_started':
        if (!event.agent) break;
        // Track DAG task as 'working'
        if (event.task_id) {
          setDagTaskStatus(prev => ({ ...prev, [event.task_id!]: 'working' }));
        }
        if (event.timestamp > activityLoadedUpToRef.current) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_started', timestamp: event.timestamp,
            agent: event.agent, task: event.task,
          }]);
        }
        setAgentStates(prev => ({
          ...prev,
          [event.agent!]: {
            name: event.agent!, state: 'working', task: event.task, current_tool: undefined,
            cost: prev[event.agent!]?.cost ?? 0, turns: prev[event.agent!]?.turns ?? 0,
            duration: prev[event.agent!]?.duration ?? 0,
            last_result: undefined,
            started_at: Date.now(),
            last_update_at: Date.now(),
          },
        }));
        setLastTicker(`${event.agent} started${event.task ? ': ' + event.task.slice(0, 60) : ''}`);
        setSdkCalls(prev => [...prev, {
          agent: event.agent!, startTime: event.timestamp, status: 'running',
        }]);
        // BUG FIX: seed liveAgentStream so the "Live" section appears immediately on agent_started
        setLiveAgentStream(prev => ({
          ...prev,
          [event.agent!]: {
            text: event.task?.slice(0, 200) || 'starting...',
            timestamp: Date.now(),
          },
        }));
        break;

      case 'agent_finished':
        if (!event.agent) break;
        // Track DAG task as completed or failed
        if (event.task_id) {
          setDagTaskStatus(prev => ({
            ...prev,
            [event.task_id!]: event.is_error ? 'failed' : 'completed',
          }));
        }
        // Remove from live stream — agent is done
        setLiveAgentStream(prev => {
          const next = { ...prev };
          delete next[event.agent!];
          return next;
        });
        if (event.timestamp > activityLoadedUpToRef.current) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_finished', timestamp: event.timestamp,
            agent: event.agent, cost: event.cost, turns: event.turns,
            duration: event.duration, is_error: event.is_error,
          }]);
        }
        setAgentStates(prev => ({
          ...prev,
          [event.agent!]: {
            ...prev[event.agent!], name: event.agent!,
            state: event.is_error ? 'error' : 'done', current_tool: undefined,
            cost: (prev[event.agent!]?.cost ?? 0) + (event.cost ?? 0),
            turns: (prev[event.agent!]?.turns ?? 0) + (event.turns ?? 0),
            duration: event.duration ?? 0,
            delegated_from: undefined, delegated_at: undefined,
            last_result: prev[event.agent!]?.last_result,
            started_at: undefined,
            last_update_at: Date.now(),
          },
        }));
        setSdkCalls(prev => {
            const updated = [...prev];
            // Find the last running entry for this agent
            let idx = -1;
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].agent === event.agent && updated[i].status === 'running') {
                idx = i;
                break;
              }
            }
            if (idx >= 0) {
              updated[idx] = {
                ...updated[idx],
                endTime: event.timestamp,
                cost: event.cost,
                status: event.is_error ? 'error' : 'done',
              };
            }
            return updated;
          });
        break;

      case 'delegation':
        if (event.timestamp > activityLoadedUpToRef.current) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'delegation', timestamp: event.timestamp,
            from_agent: event.from_agent, to_agent: event.to_agent, task: event.task,
          }]);
        }
        // Optimistically mark delegated agent as 'working' immediately so the UI
        // never shows STANDBY between the delegation event and agent_started.
        if (event.to_agent) {
          const toAgent = event.to_agent;
          setAgentStates(prev => ({
            ...prev,
            [toAgent]: {
              ...prev[toAgent],
              name: toAgent,
              state: 'working',
              task: event.task ?? prev[toAgent]?.task,
              delegated_from: event.from_agent,
              delegated_at: Date.now(),
              current_tool: undefined,
              started_at: Date.now(),
              last_update_at: Date.now(),
            },
          }));
        }
        break;

      case 'loop_progress':
        setLoopProgress({
          loop: event.loop ?? 0, max_loops: event.max_loops ?? 0,
          turn: event.turn ?? 0, max_turns: event.max_turns ?? 0,
          cost: event.cost ?? 0, max_budget: event.max_budget ?? 0,
        });
        break;

      case 'agent_result':
        if (event.text) {
          const agentMatch = event.text.match(/\*(\w+)\*/);
          const resultAgent = agentMatch ? agentMatch[1] : (event.agent || 'agent');
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_text', timestamp: event.timestamp,
            agent: resultAgent, content: event.text,
          }]);
          // Store as last result for the agent card preview
          if (resultAgent && resultAgent !== 'agent') {
            setAgentStates(prev => {
              if (prev[resultAgent]) {
                return {
                  ...prev,
                  [resultAgent]: { ...prev[resultAgent], last_result: event.text!.slice(0, 200) },
                };
              }
              return prev;
            });
          }
        }
        break;

      case 'agent_final':
        loadProject();
        loadFiles();
        if (event.text) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_text', timestamp: event.timestamp,
            agent: 'system', content: event.text,
          }]);
        }
        // Browser notification when task completes
        if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('Task Complete', {
            body: event.text?.slice(0, 100) || 'Agent finished working',
            icon: '/favicon.ico',
          });
        }
        // Preserve 'done'/'error' states — only reset agents stuck in 'working'
        // so the panel shows which agents actually ran after the task completes.
        setAgentStates(prev => {
          const reset: Record<string, AgentStateType> = {};
          for (const [k, v] of Object.entries(prev)) {
            reset[k] = { ...v, state: v.state === 'working' ? 'idle' : v.state, current_tool: undefined };
          }
          return reset;
        });
        setLoopProgress(null);
        setLastTicker('');
        setLiveAgentStream({});
        break;

      case 'project_status':
        loadProject();
        if (event.status === 'running') {
          // New task starting — wipe previous round's done/error states for a clean slate
          setAgentStates(prev => {
            const reset: Record<string, AgentStateType> = {};
            for (const [k, v] of Object.entries(prev)) {
              reset[k] = { ...v, state: 'idle', current_tool: undefined, task: undefined, last_result: undefined };
            }
            return reset;
          });
          setDagGraph(null);
          setHealingEvents([]);
          setDagTaskStatus({});
          setLiveAgentStream({});
          // Clear persisted DAG so the new plan starts fresh
          if (id) { try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ } }
        } else if (event.status === 'idle') {
          // Task ended — only reset stale 'working' states; preserve done/error
          setAgentStates(prev => {
            const reset: Record<string, AgentStateType> = {};
            for (const [k, v] of Object.entries(prev)) {
              reset[k] = { ...v, state: v.state === 'working' ? 'idle' : v.state, current_tool: undefined };
            }
            return reset;
          });
          setLoopProgress(null);
          setLastTicker('');
          setLiveAgentStream({});
        }
        break;

      case 'task_graph' as WSEvent['type']:
        if (event.graph) {
          setDagGraph(event.graph);
          // Reset task statuses for the new plan
          setDagTaskStatus({});
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_text', timestamp: event.timestamp,
            agent: 'PM', content: `📋 **DAG Plan:** ${event.graph?.vision || 'Execution plan created'} (${event.graph?.tasks?.length || 0} tasks)`,
          }]);
          setLastTicker(`Plan: ${event.graph.vision?.slice(0, 80) || 'DAG created'}`);
        }
        break;

      case 'self_healing' as WSEvent['type']:
        setHealingEvents(prev => [...prev, {
          timestamp: event.timestamp,
          failed_task: event.failed_task || '',
          failure_category: event.failure_category || 'unknown',
          remediation_task: event.remediation_task || '',
          remediation_role: event.remediation_role || '',
        }]);
        setActivities(prev => [...prev, {
          id: nextId(), type: 'agent_text', timestamp: event.timestamp,
          agent: 'system',
          content: `🔧 **Self-healing:** Task ${event.failed_task} failed (${event.failure_category}). Auto-fix: ${event.remediation_task} (${event.remediation_role})`,
        }]);
        setLastTicker(`🔧 Self-healing: ${event.failure_category} → ${event.remediation_role}`);
        break;

      case 'approval_request' as WSEvent['type']:
        if (event.description) {
          setApprovalRequest(event.description);
        }
        break;

      case 'history_cleared' as WSEvent['type']:
        // Real-time clear — reset all frontend state
        setActivities([]);
        setAgentStates({});
        setLoopProgress(null);
        setLastTicker('');
        setSdkCalls([]);
        setFiles(null);
        setMessageOffset(0);
        setDagGraph(null);
        setDagTaskStatus({});
        setHealingEvents([]);
        setLiveAgentStream({});
        setHasMoreMessages(false);
        setApprovalRequest(null);
        if (id) { try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ } }
        loadProject();
        break;

      case 'live_state_sync': {
        // Recovery event from WebSocket reconnection
        if (event.agent_states) {
          const restored: Record<string, AgentStateType> = {};
          for (const [name, s] of Object.entries(event.agent_states as Record<string, any>)) {
            const isWorking = (s.state ?? 'idle') === 'working';
            restored[name] = {
              name,
              state: s.state ?? 'idle',
              task: s.task,
              current_tool: s.current_tool,
              cost: s.cost ?? 0,
              turns: s.turns ?? 0,
              duration: s.duration ?? 0,
              started_at: isWorking ? Date.now() : undefined,
              last_update_at: isWorking ? Date.now() : undefined,
            };
          }
          setAgentStates(prev => ({ ...prev, ...restored }));
          // BUG FIX: also seed liveAgentStream for agents that are still working on reconnect
          const liveEntries: Record<string, { text: string; timestamp: number }> = {};
          for (const [name, s] of Object.entries(event.agent_states as Record<string, any>)) {
            if ((s.state ?? 'idle') === 'working') {
              liveEntries[name] = { text: s.task || 'working...', timestamp: Date.now() };
            }
          }
          if (Object.keys(liveEntries).length > 0) {
            setLiveAgentStream(prev => ({ ...prev, ...liveEntries }));
          }
        }
        if (event.loop_progress) {
          setLoopProgress(event.loop_progress);
        }
        if (event.status === 'running') {
          setLastTicker('agents working...');
        }
        // Restore DAG graph and task statuses from backend live state
        if (event.dag_graph) {
          setDagGraph(event.dag_graph);
        }
        if (event.dag_task_statuses && Object.keys(event.dag_task_statuses).length > 0) {
          setDagTaskStatus(prev => ({
            ...prev,
            ...event.dag_task_statuses as Record<string, 'pending' | 'working' | 'completed' | 'failed'>,
          }));
        }
        break;
      }

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
              setLoadError(null);
              loadProject().catch((e) => setLoadError(e.message || 'Failed to load project'));
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

  const handleSend = async (message: string) => {
    // All messages go through the Orchestrator — no direct agent targeting
    setActivities(prev => [...prev, {
      id: nextId(), type: 'user_message', timestamp: Date.now() / 1000,
      agent: 'user', content: message,
    }]);
    try {
      await sendMessage(id, message);
      toast.success('Message sent');
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to send message:', errMsg);
      toast.error('Send failed', errMsg);
      setActivities(prev => [...prev, {
        id: nextId(), type: 'error', timestamp: Date.now() / 1000,
        agent: 'system', content: `Failed to send: ${errMsg}`,
      }]);
    }
    loadProject();
  };

  const handlePause = async () => {
    try {
      await pauseProject(id);
      toast.info('Project paused');
      loadProject();
    } catch (err: unknown) {
      toast.error('Pause failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleResume = async () => {
    try {
      await resumeProject(id);
      toast.success('Project resumed');
      loadProject();
    } catch (err: unknown) {
      toast.error('Resume failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleStop = async () => {
    try {
      await stopProject(id);
      toast.warning('Project stopped');
      loadProject();
    } catch (err: unknown) {
      toast.error('Stop failed', err instanceof Error ? err.message : String(err));
    }
  };
  const handleClearHistory = async () => {
    setShowClearConfirm(false);
    try {
      await clearHistory(id);
      // Reset ALL frontend state — agent has no memory after clear
      setActivities([]);
      setAgentStates({});
      setLoopProgress(null);
      setLastTicker('');
      setSdkCalls([]);
      setFiles(null);
      setMessageOffset(0);
      setHasMoreMessages(false);
      setApprovalRequest(null);
      toast.success('History cleared');
      loadProject();
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

  // Per-agent performance metrics (cost, duration, success rate) for the Agents tab
  const agentMetrics = useAgentMetrics(activities);

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
                  setResumableTask(null);
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
                  setResumableTask(null);
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
            <PlanView activities={activities} />
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
                  setMobileView(item.id);
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
              <button onClick={() => setShowClearConfirm(true)} className="p-1.5 ml-1" style={{ color: 'var(--text-muted)' }}
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
                    setSending(true);
                    handleSend(msg).finally(() => setSending(false));
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
                  setSending(true);
                  handleSend(msg).finally(() => setSending(false));
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
                onClick={() => setDesktopTab(tab.id)}
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
              <button onClick={() => setShowClearConfirm(true)} className="ml-auto p-1.5 rounded-lg transition-all hover:bg-[var(--bg-elevated)]"
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
          const hasStatus = workingAgents.length > 0 || doneAgents.length > 0 || errorAgents.length > 0;
          if (!hasStatus) return null;

          return (
            <div className="flex-shrink-0 px-4 py-1.5 flex items-center gap-3 overflow-x-auto"
              style={{ borderBottom: '1px solid var(--border-dim)', background: 'linear-gradient(180deg, var(--bg-panel), var(--bg-void))' }}>
              {workingAgents.map(agent => {
                const ac = getAgentAccent(agent.name);
                const elapsedSec = agent.started_at ? Math.round((Date.now() - agent.started_at) / 1000) : 0;
                const isStale = agent.last_update_at ? (Date.now() - agent.last_update_at) > 60000 : false;
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
                      <span className="text-[10px] truncate max-w-[180px]" style={{ color: `${ac.color}99`, fontFamily: 'var(--font-mono)' }}>
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
                  onSelectAgent={setSelectedAgent}
                  selectedAgent={selectedAgent}
                  layout="grid"
                />
                {agentMetrics.length > 0 && (
                  <AgentMetrics metrics={agentMetrics} />
                )}
              </div>
            )}
            {desktopTab === 'plan' && (
              <PlanView activities={activities} />
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
              {Object.keys(liveAgentStream).filter(a => agentStates[a]?.state === 'working').length > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
                  <span className="text-[10px] font-mono" style={{ color: 'var(--accent-green)' }}>
                    {Object.keys(liveAgentStream).filter(a => agentStates[a]?.state === 'working').length} running
                  </span>
                </div>
              )}
            </div>

            {/* Live Agent Stream — sticky section showing what each agent is doing NOW */}
            {(() => {
              const activeAgents = Object.entries(liveAgentStream).filter(([name]) => agentStates[name]?.state === 'working');
              if (activeAgents.length === 0) return null;
              return (
                <div className="flex-shrink-0 overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-elevated)', maxHeight: '220px', overflowY: 'auto' }}>
                  <div className="px-3 pt-2 pb-1">
                    <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>⚡ Live</span>
                  </div>
                  {activeAgents.map(([agentName, entry]) => {
                    const ac = getAgentAccent(agentName);
                    const agentState = agentStates[agentName];
                    const elapsedSec = agentState?.started_at ? Math.round((Date.now() - agentState.started_at) / 1000) : 0;
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
          onClose={() => setApprovalRequest(null)}
        />
      )}

      {/* Clear History Confirmation Modal */}
      {showClearConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]"
          style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
          onClick={() => setShowClearConfirm(false)}
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
                  onClick={() => setShowClearConfirm(false)}
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
