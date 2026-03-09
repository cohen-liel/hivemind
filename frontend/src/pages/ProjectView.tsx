import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { getProject, getMessages, getFiles, sendMessage, talkToAgent, pauseProject, resumeProject, stopProject, getLiveState, clearHistory, getResumableTask, resumeInterruptedTask, discardInterruptedTask } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { useIOSViewport } from '../useIOSViewport';
import ActivityFeed from '../components/ActivityFeed';
import AgentStatusPanel from '../components/AgentStatusPanel';
import ConductorBar from '../components/ConductorBar';
import FileDiff from '../components/FileDiff';
import PlanView from '../components/PlanView';
import NetworkTrace from '../components/NetworkTrace';
import ApprovalModal from '../components/ApprovalModal';
import Controls from '../components/Controls';
import CodeBrowser from '../components/CodeBrowser';
import ConductorMode from '../components/ConductorMode';
import type { Project, ProjectMessage, FileChanges, WSEvent, ActivityEntry, AgentState as AgentStateType, LoopProgress } from '../types';

let activityIdCounter = 0;
function nextId(): string {
  return `a-${++activityIdCounter}`;
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

type MobileView = 'orchestra' | 'activity' | 'code' | 'changes' | 'plan' | 'trace';
type DesktopTab = 'nexus' | 'agents' | 'plan' | 'code' | 'diff' | 'trace';

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
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
  const [resumableTask, setResumableTask] = useState<{ last_message: string; current_loop: number; total_cost_usd: number } | null>(null);

  const loadProject = useCallback(async () => {
    if (!id) return;
    const p = await getProject(id);
    setProject(p);

    // Restore pending approval from project data
    if (p.pending_approval) {
      setApprovalRequest(p.pending_approval);
    }

    // Failsafe: merge agent_states from poll response to recover from missed WS events.
    // Rule: only apply server state when it is non-idle (don't overwrite fresher
    // WS 'done'/'working' data with a stale server-side 'idle' between rounds).
    if (p.agent_states && Object.keys(p.agent_states).length > 0) {
      setAgentStates(prev => {
        let changed = false;
        const updated = { ...prev };
        for (const [name, s] of Object.entries(p.agent_states!)) {
          const serverState = (s.state ?? 'idle') as AgentStateType['state'];
          if (serverState === 'idle') continue;   // never downgrade on a stale server idle
          const ourState = updated[name]?.state ?? 'idle';
          if (ourState !== serverState) {
            updated[name] = {
              ...updated[name],
              name,
              state: serverState,
              task: s.task ?? updated[name]?.task,
              current_tool: s.current_tool ?? undefined,
              cost: s.cost ?? updated[name]?.cost ?? 0,
              turns: s.turns ?? updated[name]?.turns ?? 0,
              duration: updated[name]?.duration ?? 0,
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
    getMessages(id, 100).then((data) => {
      setActivities(messagesToActivities(data.messages));
      setHasMoreMessages(data.total > 100);
      setMessageOffset(100);
    }).catch(() => {});
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
          restored[name] = {
            name,
            state: (s.state as AgentStateType['state']) ?? 'idle',
            task: s.task,
            current_tool: s.current_tool,
            cost: s.cost ?? 0,
            turns: s.turns ?? 0,
            duration: s.duration ?? 0,
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
          setAgentStates(prev => ({
            ...prev,
            [updateAgent]: {
              ...prev[updateAgent],
              name: updateAgent,
              state: 'working',
              current_tool: event.text?.slice(0, 150),
            },
          }));
          // Show agent name + action in ticker
          const action = event.text?.slice(0, 100) || 'working...';
          setLastTicker(`${updateAgent}: ${action}`);
        }
        break;
      }

      case 'tool_use':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'tool_use', timestamp: event.timestamp,
          agent: event.agent, tool_name: event.tool_name, tool_description: event.description,
        }]);
        if (event.agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.agent!]: { ...prev[event.agent!], name: event.agent!, current_tool: event.description },
          }));
          setLastTicker(`${event.agent}: ${event.description || event.tool_name}`);
        }
        break;

      case 'agent_started':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'agent_started', timestamp: event.timestamp,
          agent: event.agent, task: event.task,
        }]);
        if (event.agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.agent!]: {
              name: event.agent!, state: 'working', task: event.task, current_tool: undefined,
              cost: prev[event.agent!]?.cost ?? 0, turns: prev[event.agent!]?.turns ?? 0,
              duration: prev[event.agent!]?.duration ?? 0,
              last_result: undefined,
            },
          }));
          setLastTicker(`${event.agent} started${event.task ? ': ' + event.task.slice(0, 60) : ''}`);
          setSdkCalls(prev => [...prev, {
            agent: event.agent!, startTime: event.timestamp, status: 'running',
          }]);
        }
        break;

      case 'agent_finished':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'agent_finished', timestamp: event.timestamp,
          agent: event.agent, cost: event.cost, turns: event.turns,
          duration: event.duration, is_error: event.is_error,
        }]);
        if (event.agent) {
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
        }
        break;

      case 'delegation':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'delegation', timestamp: event.timestamp,
          from_agent: event.from_agent, to_agent: event.to_agent, task: event.task,
        }]);
        // Optimistically mark delegated agent as 'working' immediately so the UI
        // never shows STANDBY between the delegation event and agent_started.
        if (event.to_agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.to_agent!]: {
              ...prev[event.to_agent!],
              name: event.to_agent!,
              state: 'working',
              task: event.task ?? prev[event.to_agent!]?.task,
              delegated_from: event.from_agent,
              delegated_at: Date.now(),
              current_tool: undefined,
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
        }
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
        setHasMoreMessages(false);
        setApprovalRequest(null);
        loadProject();
        break;

      default:
        break;
    }
  }, [id, loadProject, loadFiles]);

  const { connected } = useWSSubscribe(handleWSEvent);
  useIOSViewport();

  if (loadError) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-center px-4">
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-6 py-5 max-w-sm mx-auto">
            <div className="text-red-400 text-sm font-medium mb-2">Failed to load project</div>
            <div className="text-red-400/70 text-xs mb-4">{loadError}</div>
            <button
              onClick={() => {
                setLoadError(null);
                loadProject().catch((e) => setLoadError(e.message || 'Failed to load project'));
              }}
              className="px-4 py-1.5 bg-red-500/20 hover:bg-red-500/30 text-red-300 text-xs font-medium rounded-lg transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!project || !id) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-500">
        <div className="animate-pulse text-sm">Loading...</div>
      </div>
    );
  }

  const handleSend = async (message: string, agent?: string) => {
    setActivities(prev => [...prev, {
      id: nextId(), type: 'user_message', timestamp: Date.now() / 1000,
      agent: 'user', content: message,
    }]);
    try {
      if (agent) {
        await talkToAgent(id, agent, message);
      } else {
        await sendMessage(id, message);
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to send message:', errMsg);
      setActivities(prev => [...prev, {
        id: nextId(), type: 'error', timestamp: Date.now() / 1000,
        agent: 'system', content: `Failed to send: ${errMsg}`,
      }]);
    }
    loadProject();
  };

  const handlePause = async () => { await pauseProject(id); loadProject(); };
  const handleResume = async () => { await resumeProject(id); loadProject(); };
  const handleStop = async () => { await stopProject(id); loadProject(); };
  const handleClearHistory = async () => {
    if (!confirm('Clear all history and start fresh?')) return;
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
      loadProject();
    } catch (e) {
      console.error('Failed to clear history:', e);
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
        <div className="bg-amber-900/40 border-b border-amber-500/30 px-4 py-3 flex items-center justify-between gap-3 z-50">
          <div className="flex-1 min-w-0">
            <div className="text-amber-200 text-sm font-medium">⚠️ Interrupted Task Found</div>
            <div className="text-amber-200/60 text-xs truncate">
              {resumableTask.last_message.slice(0, 100)}
              {' — '}{resumableTask.current_loop} rounds, ${resumableTask.total_cost_usd.toFixed(4)}
            </div>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs rounded-md transition-colors"
              onClick={async () => {
                if (!id) return;
                try {
                  await resumeInterruptedTask(id);
                  setResumableTask(null);
                  loadProject();
                } catch (e: unknown) {
                  const msg = e instanceof Error ? e.message : 'Unknown error';
                  console.error('Resume failed:', msg);
                  alert(`Failed to resume task: ${msg}`);
                }
              }}
            >
              Resume
            </button>
            <button
              className="px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 text-xs rounded-md transition-colors"
              onClick={async () => {
                if (!id) return;
                try {
                  await discardInterruptedTask(id);
                  setResumableTask(null);
                } catch (e: unknown) {
                  const msg = e instanceof Error ? e.message : 'Unknown error';
                  console.error('Discard failed:', msg);
                  alert(`Failed to discard task: ${msg}`);
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
          inset: 0,
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
                  <button onClick={handlePause} className="p-1.5" style={{ color: 'var(--accent-amber)' }}>
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                      <rect x="4" y="3" width="3" height="10" rx="0.5"/>
                      <rect x="9" y="3" width="3" height="10" rx="0.5"/>
                    </svg>
                  </button>
                )}
                {project.status === 'paused' && (
                  <button onClick={handleResume} className="p-1.5" style={{ color: 'var(--accent-green)' }}>
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                      <path d="M4 3l9 5-9 5V3z"/>
                    </svg>
                  </button>
                )}
                <button onClick={handleStop} className="p-1.5" style={{ color: 'var(--accent-red)' }}>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                    <rect x="3" y="3" width="10" height="10" rx="1"/>
                  </svg>
                </button>
              </div>
            )}
            {/* Clear history button — visible when idle */}
            {project.status === 'idle' && activities.length > 0 && (
              <button onClick={handleClearHistory} className="p-1.5 ml-1" style={{ color: 'var(--text-muted)' }}
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
          </div>
        </div>

        {/* Split view: tab content (left) + activity log (right) */}
        <div className="flex-1 flex min-h-0 overflow-hidden" style={{ width: '100%' }}>
          {/* Left panel: selected tab content */}
          <div className="overflow-y-auto overflow-x-hidden min-w-0" style={{ width: '65%', maxWidth: '65%', flexShrink: 0 }}>
            {desktopTab === 'nexus' && (
              <ConductorMode
                agents={agentStateList}
                progress={loopProgress}
                activities={activities}
                totalCost={project.total_cost_usd}
                status={project.status}
                messageDraft={message}
              />
            )}
            {desktopTab === 'agents' && (
              <div className="p-6">
                <AgentStatusPanel
                  agents={agentStateList}
                  onSelectAgent={setSelectedAgent}
                  selectedAgent={selectedAgent}
                  layout="grid"
                />
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
            <div className="px-4 py-2" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)', zIndex: 10 }}>
              <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Activity Log</h3>
            </div>
            <div className="flex-1 overflow-y-auto min-h-0">
              <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={loadEarlierMessages} />
            </div>
            {/* Chat input — anchored to bottom of activity panel */}
            <Controls
              projectId={id}
              status={project.status}
              agents={project.agents}
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
    </div>
  );
}
