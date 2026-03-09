/**
 * ProjectView.tsx — Main project detail page.
 *
 * Orchestrates state management (useReducer), WebSocket event handling,
 * data loading, and layout routing. All rendering is delegated to
 * extracted sub-components.
 */

import { useEffect, useReducer, useRef, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  getProject, getMessages, getFiles, sendMessage, pauseProject,
  resumeProject, stopProject, getLiveState, clearHistory,
  getResumableTask, resumeInterruptedTask, discardInterruptedTask, getActivity,
} from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { useIOSViewport } from '../useIOSViewport';
import { useToast } from '../components/Toast';
import { useAgentMetrics } from '../hooks/useAgentMetrics';
import { projectReducer, initialProjectState } from '../reducers/projectReducer';
import type { ProjectMessage, WSEvent, AgentState as AgentStateType } from '../types';
import type { ActivityEvent } from '../api';
import {
  nextId, messagesToActivities, activityEventsToEntries,
  reconstructSdkCalls, reconstructAgentStates,
} from '../utils/activityHelpers';

// ── Extracted sub-components ──
import ConductorBar from '../components/ConductorBar';
import ConductorMode from '../components/ConductorMode';
import PlanView from '../components/PlanView';
import ApprovalModal from '../components/ApprovalModal';
import ActivityFeed from '../components/ActivityFeed';
import { ProjectErrorState, ProjectLoadingSkeleton, ResumableTaskBanner } from '../components/ProjectHeader';
import MobileTabNav from '../components/MobileTabNav';
import { LiveStatusStrip, DesktopTabBar, NexusTabContent, AgentsTabContent } from '../components/AgentOrchestra';
import ActivityPanel, { MobileLiveAgentStream } from '../components/ActivityPanel';
import CodePanel from '../components/CodePanel';
import ChangesPanel from '../components/ChangesPanel';
import TracePanel from '../components/TracePanel';

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

  // ── 1-minute heartbeat ──
  // Emits an "⏱️ still working" activity for agents that have been running >60s without
  // producing a new activity entry. Fires at most once per minute per agent.
  const lastHeartbeatRef = useRef<Record<string, number>>({});
  useEffect(() => {
    const workingAgents = Object.entries(agentStates).filter(([, a]) => a.state === 'working');
    for (const [agentName, agentState] of workingAgents) {
      const startedAt = agentState.started_at ?? now;
      const runningMs = now - startedAt;
      if (runningMs < 60_000) continue;          // don't fire until 1st minute passes

      const lastHb = lastHeartbeatRef.current[agentName];
      if (lastHb === undefined || now - lastHb >= 60_000) {
        lastHeartbeatRef.current[agentName] = now;
        const totalMin = Math.floor(runningMs / 60_000);
        const remSec = Math.floor((runningMs % 60_000) / 1_000);
        const timeStr = remSec > 0 ? `${totalMin}m ${remSec}s` : `${totalMin}m`;
        const currentAction = (agentState.current_tool || agentState.task || 'thinking...').slice(0, 80);
        dispatch({
          type: 'ADD_ACTIVITY',
          activity: {
            id: nextId(),
            type: 'agent_text',
            timestamp: now / 1000,
            agent: agentName,
            content: `⏱️ Still working (${timeStr}) — ${currentAction}`,
          },
        });
      }
    }
    // Remove stopped agents from heartbeat tracking
    for (const agentName of Object.keys(lastHeartbeatRef.current)) {
      if (agentStates[agentName]?.state !== 'working') {
        delete lastHeartbeatRef.current[agentName];
      }
    }
  }, [now, agentStates, dispatch]);

  // Per-agent performance metrics
  const agentMetrics = useAgentMetrics(activities);

  // ── DAG hydration/persistence ──
  useEffect(() => {
    if (!id) return;
    try {
      const saved = localStorage.getItem(`nexus_dag_${id}`);
      if (saved) {
        const { graph, statuses, savedAt } = JSON.parse(saved);
        const AGE_LIMIT_MS = 24 * 60 * 60 * 1000;
        if (Date.now() - savedAt < AGE_LIMIT_MS && graph) {
          dispatch({ type: 'HYDRATE_DAG', graph, statuses: statuses ?? {} });
        }
      }
    } catch { /* corrupted storage — ignore */ }
  }, [id]);

  useEffect(() => {
    if (!id || !state.dagGraph) return;
    try {
      localStorage.setItem(`nexus_dag_${id}`, JSON.stringify({
        graph: state.dagGraph, statuses: state.dagTaskStatus, savedAt: Date.now(),
      }));
    } catch { /* quota exceeded — ignore */ }
  }, [id, state.dagGraph, state.dagTaskStatus]);

  // ── Data loading callbacks ──
  const loadProject = useCallback(async (): Promise<void> => {
    if (!id) return;
    const p = await getProject(id);
    dispatch({ type: 'SET_PROJECT', project: p });
    if (p.pending_approval) {
      dispatch({ type: 'SET_APPROVAL_REQUEST', request: p.pending_approval });
    }
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

  // ── Initial data load ──
  useEffect(() => {
    if (!id) return;
    dispatch({ type: 'SET_LOAD_ERROR', error: null });
    loadProject().catch((e) => dispatch({ type: 'SET_LOAD_ERROR', error: e.message || 'Failed to load project' }));

    Promise.all([
      getMessages(id, 100).catch(() => ({ messages: [] as ProjectMessage[], total: 0 })),
      getActivity(id, 0, 500).catch(() => ({ events: [] as ActivityEvent[], latest_sequence: 0, source: 'none' })),
    ]).then(([msgData, actData]) => {
      const msgEntries = messagesToActivities(msgData.messages);
      const actEntries = activityEventsToEntries(actData.events);
      const merged = [...msgEntries, ...actEntries].sort((a, b) => a.timestamp - b.timestamp);
      dispatch({
        type: 'LOAD_INITIAL_DATA',
        activities: merged,
        sdkCalls: reconstructSdkCalls(actData.events),
        agentStates: reconstructAgentStates(actData.events),
        hasMoreMessages: msgData.total > 100,
        messageOffset: 100,
        lastSequenceId: actData.latest_sequence ?? 0,
      });
    });

    loadFiles().catch(() => {});

    Promise.all([getResumableTask(id), getProject(id)]).then(([data, proj]) => {
      if (data.resumable && data.task && proj.status !== 'running') {
        dispatch({ type: 'SET_RESUMABLE_TASK', task: data.task });
      } else {
        dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      }
    }).catch(() => {});

    getLiveState(id).then((live) => {
      if (live.agent_states && Object.keys(live.agent_states).length > 0) {
        const restored: Record<string, AgentStateType> = {};
        for (const [name, s] of Object.entries(live.agent_states)) {
          const isWorking = (s.state as AgentStateType['state']) === 'working';
          restored[name] = {
            name,
            state: (s.state as AgentStateType['state']) ?? 'idle',
            task: s.task, current_tool: s.current_tool,
            cost: s.cost ?? 0, turns: s.turns ?? 0, duration: s.duration ?? 0,
            started_at: isWorking ? Date.now() : undefined,
            last_update_at: isWorking ? Date.now() : undefined,
          };
        }
        dispatch({ type: 'MERGE_AGENT_STATES_FROM_LIVE', restored });
      }
      if (live.loop_progress) dispatch({ type: 'RESTORE_LOOP_PROGRESS', progress: live.loop_progress });
      if (live.pending_approval) dispatch({ type: 'SET_APPROVAL_REQUEST', request: live.pending_approval });
    }).catch(() => {});

    const statusPoll = setInterval(() => { loadProject().catch(() => {}); }, 10000);
    return () => clearInterval(statusPoll);
  }, [id, loadProject, loadFiles]);

  // ── WebSocket handler ──
  const handleWSEvent = useCallback((event: WSEvent): void => {
    if (event.project_id !== id) return;
    switch (event.type) {
      case 'agent_update': dispatch({ type: 'WS_AGENT_UPDATE', event }); break;
      case 'tool_use': dispatch({ type: 'WS_TOOL_USE', event }); break;
      case 'agent_started': dispatch({ type: 'WS_AGENT_STARTED', event }); break;
      case 'agent_finished': dispatch({ type: 'WS_AGENT_FINISHED', event }); break;
      case 'delegation': dispatch({ type: 'WS_DELEGATION', event }); break;
      case 'loop_progress': dispatch({ type: 'WS_LOOP_PROGRESS', event }); break;
      case 'agent_result': dispatch({ type: 'WS_AGENT_RESULT', event }); break;
      case 'agent_final':
        dispatch({ type: 'WS_AGENT_FINAL', event });
        loadProject().catch(() => {}); loadFiles().catch(() => {});
        if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('Task Complete', { body: event.text?.slice(0, 100) || 'Agent finished working', icon: '/favicon.ico' });
        }
        break;
      case 'project_status':
        dispatch({ type: 'WS_PROJECT_STATUS', event });
        loadProject().catch(() => {});
        if (event.status === 'running' && id) {
          try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ }
        }
        break;
      case 'task_graph' as WSEvent['type']: dispatch({ type: 'WS_TASK_GRAPH', event }); break;
      case 'task_error' as WSEvent['type']:
        // Show backend task failures in the activity log and as a toast so users see the cause
        dispatch({
          type: 'ADD_ACTIVITY',
          activity: {
            id: nextId(),
            type: 'error',
            timestamp: event.timestamp ?? Date.now() / 1000,
            agent: event.agent || 'system',
            content: `❌ ${event.agent ? `${event.agent} failed` : 'Task failed'}: ${event.text || event.summary || 'Unknown error'}`,
          },
        });
        toast.error(
          `${event.agent || 'Task'} failed`,
          (event.text || event.summary || 'An error occurred').slice(0, 120),
        );
        break;
      case 'self_healing' as WSEvent['type']: dispatch({ type: 'WS_SELF_HEALING', event }); break;
      case 'approval_request' as WSEvent['type']: dispatch({ type: 'WS_APPROVAL_REQUEST', event }); break;
      case 'history_cleared' as WSEvent['type']:
        dispatch({ type: 'WS_HISTORY_CLEARED' });
        if (id) { try { localStorage.removeItem(`nexus_dag_${id}`); } catch { /* ignore */ } }
        loadProject().catch(() => {});
        break;
      case 'live_state_sync': dispatch({ type: 'WS_LIVE_STATE_SYNC', event }); break;
      default: break;
    }
  }, [id, loadProject, loadFiles]);

  const { connected } = useWSSubscribe(handleWSEvent);
  useIOSViewport();

  // ── Error / Loading early returns ──
  if (loadError) {
    return (
      <ProjectErrorState
        error={loadError}
        onRetry={() => {
          dispatch({ type: 'SET_LOAD_ERROR', error: null });
          loadProject().catch((e) => dispatch({ type: 'SET_LOAD_ERROR', error: e.message || 'Failed to load project' }));
        }}
      />
    );
  }
  if (!project || !id) return <ProjectLoadingSkeleton />;

  // ── Action handlers ──
  const handleSend = async (msg: string): Promise<void> => {
    dispatch({ type: 'ADD_ACTIVITY', activity: { id: nextId(), type: 'user_message', timestamp: Date.now() / 1000, agent: 'user', content: msg } });
    try {
      await sendMessage(id, msg);
      toast.success('Message sent');
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      toast.error('Send failed', errMsg);
      dispatch({ type: 'ADD_ACTIVITY', activity: { id: nextId(), type: 'error', timestamp: Date.now() / 1000, agent: 'system', content: `Failed to send: ${errMsg}` } });
    }
    loadProject().catch(() => {});
  };

  const handlePause = async (): Promise<void> => {
    try { await pauseProject(id); toast.info('Project paused'); loadProject().catch(() => {}); }
    catch (err: unknown) { toast.error('Pause failed', err instanceof Error ? err.message : String(err)); }
  };
  const handleResume = async (): Promise<void> => {
    try { await resumeProject(id); toast.success('Project resumed'); loadProject().catch(() => {}); }
    catch (err: unknown) { toast.error('Resume failed', err instanceof Error ? err.message : String(err)); }
  };
  const handleStop = async (): Promise<void> => {
    try { await stopProject(id); toast.warning('Project stopped'); loadProject().catch(() => {}); }
    catch (err: unknown) { toast.error('Stop failed', err instanceof Error ? err.message : String(err)); }
  };
  const handleClearHistory = async (): Promise<void> => {
    dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false });
    try {
      await clearHistory(id);
      dispatch({ type: 'CLEAR_ALL_STATE' });
      toast.success('History cleared');
      loadProject().catch(() => {});
    } catch (e) {
      toast.error('Clear failed', e instanceof Error ? e.message : String(e));
    }
  };

  const handleResumeTask = async (): Promise<void> => {
    try {
      await resumeInterruptedTask(id);
      dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      toast.success('Task resumed');
      loadProject().catch(() => {});
    } catch (e: unknown) {
      toast.error('Failed to resume task', e instanceof Error ? e.message : 'Unknown error');
    }
  };
  const handleDiscardTask = async (): Promise<void> => {
    try {
      await discardInterruptedTask(id);
      dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      toast.info('Task discarded');
    } catch (e: unknown) {
      toast.error('Failed to discard task', e instanceof Error ? e.message : 'Unknown error');
    }
  };

  const handleMobileSend = (msg: string): void => {
    dispatch({ type: 'SET_SENDING', sending: true });
    handleSend(msg).finally(() => dispatch({ type: 'SET_SENDING', sending: false }));
  };

  // ── Computed values ──
  const agentStateList: AgentStateType[] = project.agents.map(name => (
    agentStates[name] ?? { name, state: 'idle', cost: 0, turns: 0, duration: 0 }
  ));
  const orchestratorState = agentStateList.find(a => a.name === 'orchestrator') ?? null;
  const subAgentStates = agentStateList.filter(a => a.name !== 'orchestrator');

  // ══════════════════════════════════════════════════════════════════════════
  // LAYOUT
  // ══════════════════════════════════════════════════════════════════════════

  return (
    <div className="h-full flex flex-col" style={{ background: 'var(--bg-void)', overflow: 'hidden', position: 'fixed', inset: 0 }}>

      {/* Resume interrupted task banner */}
      {resumableTask && (
        <ResumableTaskBanner resumableTask={resumableTask} onResume={handleResumeTask} onDiscard={handleDiscardTask} />
      )}

      {/* ===== MOBILE LAYOUT ===== */}
      <div
        className="lg:hidden flex flex-col z-30"
        style={{
          position: 'fixed', top: 'var(--app-offset, 0px)', left: 0, right: 0,
          height: 'var(--app-height, 100vh)', background: 'var(--bg-void)',
          paddingTop: 'env(safe-area-inset-top, 0px)', overflow: 'hidden', touchAction: 'none',
        }}
      >
        <ConductorBar
          projectName={project.project_name} status={project.status} connected={connected}
          orchestrator={orchestratorState} progress={loopProgress}
          totalCost={project.total_cost_usd} agentSummary={subAgentStates} lastTicker={lastTicker}
        />
        <div className="flex-1 overflow-y-auto min-h-0" style={{ overscrollBehavior: 'none', touchAction: 'pan-y', WebkitOverflowScrolling: 'touch' }}>
          {mobileView === 'orchestra' && (
            <ConductorMode agents={agentStateList} progress={loopProgress} activities={activities}
              totalCost={project.total_cost_usd} status={project.status} messageDraft={message} />
          )}
          {mobileView === 'activity' && (
            <div className="flex flex-col h-full">
              <MobileLiveAgentStream agentStates={agentStates} liveAgentStream={liveAgentStream} now={now} />
              <div className="flex-1 min-h-0 overflow-hidden">
                <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={loadEarlierMessages} />
              </div>
            </div>
          )}
          {mobileView === 'code' && <CodePanel projectId={id} />}
          {mobileView === 'changes' && <ChangesPanel files={files} variant="mobile" />}
          {mobileView === 'plan' && <PlanView activities={activities} dagGraph={dagGraph} dagTaskStatus={dagTaskStatus} />}
          {mobileView === 'trace' && <TracePanel calls={sdkCalls} variant="mobile" />}
        </div>
        <MobileTabNav
          mobileView={mobileView}
          onSetMobileView={(view) => dispatch({ type: 'SET_MOBILE_VIEW', view })}
          projectStatus={project.status} activitiesCount={activities.length}
          onPause={handlePause} onResume={handleResume} onStop={handleStop}
          onShowClearConfirm={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })}
          lastTicker={lastTicker} message={message} onMessageChange={setMessage}
          sending={sending} onSend={handleMobileSend}
        />
      </div>

      {/* ===== DESKTOP LAYOUT ===== */}
      <div className="hidden lg:flex flex-col h-full w-full overflow-hidden">
        <ConductorBar
          projectName={project.project_name} status={project.status} connected={connected}
          orchestrator={orchestratorState} progress={loopProgress}
          totalCost={project.total_cost_usd} agentSummary={subAgentStates} lastTicker={lastTicker}
        />
        <DesktopTabBar
          desktopTab={desktopTab}
          onSetDesktopTab={(tab) => dispatch({ type: 'SET_DESKTOP_TAB', tab })}
          projectStatus={project.status} activitiesCount={activities.length}
          onShowClearConfirm={() => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })}
        />
        <LiveStatusStrip orchestratorState={orchestratorState} subAgentStates={subAgentStates} now={now} lastTicker={lastTicker} />

        {/* Split view: tab content (left) + activity log (right) */}
        <div className="flex-1 flex min-h-0 overflow-hidden" style={{ width: '100%' }}>
          <div className="overflow-y-auto overflow-x-hidden min-w-0" style={{ width: '65%', maxWidth: '65%', flexShrink: 0 }}>
            {desktopTab === 'nexus' && (
              <NexusTabContent
                agentStateList={agentStateList} loopProgress={loopProgress} activities={activities}
                totalCost={project.total_cost_usd} projectStatus={project.status} messageDraft={message}
                dagGraph={dagGraph} dagTaskStatus={dagTaskStatus} healingEvents={healingEvents}
              />
            )}
            {desktopTab === 'agents' && (
              <AgentsTabContent
                agentStateList={agentStateList} selectedAgent={selectedAgent}
                onSelectAgent={(agent) => dispatch({ type: 'SET_SELECTED_AGENT', agent })}
                agentMetrics={agentMetrics}
              />
            )}
            {desktopTab === 'plan' && <PlanView activities={activities} dagGraph={dagGraph} dagTaskStatus={dagTaskStatus} />}
            {desktopTab === 'code' && <CodePanel projectId={id} />}
            {desktopTab === 'diff' && <ChangesPanel files={files} variant="desktop" />}
            {desktopTab === 'trace' && <TracePanel calls={sdkCalls} variant="desktop" />}
          </div>
          <ActivityPanel
            agentStates={agentStates} liveAgentStream={liveAgentStream} now={now}
            activities={activities} hasMoreMessages={hasMoreMessages} onLoadMore={loadEarlierMessages}
            projectStatus={project.status} onPause={handlePause} onResume={handleResume}
            onStop={handleStop} onSend={handleSend}
          />
        </div>
      </div>

      {/* Approval Modal */}
      {approvalRequest && id && (
        <ApprovalModal description={approvalRequest} projectId={id}
          onClose={() => dispatch({ type: 'SET_APPROVAL_REQUEST', request: null })} />
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
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)', boxShadow: '0 25px 50px rgba(0,0,0,0.4)' }}
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-labelledby="clear-confirm-title"
          >
            <div className="h-1 w-full" style={{ background: 'linear-gradient(90deg, var(--accent-red), var(--accent-amber))' }} />
            <div className="p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center text-lg flex-shrink-0" style={{ background: 'var(--glow-red)' }}>
                  🗑️
                </div>
                <div>
                  <h3 id="clear-confirm-title" className="text-base font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
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
                  className="px-4 py-2 text-sm font-medium rounded-xl transition-all focus:outline-none focus:ring-2 focus:ring-[var(--border-dim)]"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border-dim)' }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleClearHistory}
                  className="px-4 py-2 text-sm font-semibold rounded-xl transition-all text-white active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-[var(--accent-red)]"
                  style={{ background: 'var(--accent-red)', boxShadow: '0 2px 10px var(--glow-red)' }}
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
