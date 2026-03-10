/**
 * ProjectView.tsx — Main project detail page (orchestration layer).
 *
 * This component is the thin orchestration layer that wires together:
 * - State management (useReducer via projectReducer)
 * - Data loading (initial fetch + polling)
 * - WebSocket subscriptions (via useProjectWebSocket)
 * - Smart heartbeat (via useSmartHeartbeat)
 * - DAG persistence (via useDagPersistence)
 * - Action handlers (via useProjectActions)
 *
 * All rendering is delegated to extracted sub-components
 * in components/project/ and components/.
 */

import { useEffect, useReducer, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  getProject, getMessages, getFiles, getLiveState,
  getResumableTask, getActivity,
} from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { useIOSViewport } from '../useIOSViewport';
import { useToast } from '../components/Toast';
import { useAgentMetrics } from '../hooks/useAgentMetrics';
import { useSmartHeartbeat } from '../hooks/useSmartHeartbeat';
import { useDagPersistence } from '../hooks/useDagPersistence';
import { useProjectWebSocket } from '../hooks/useProjectWebSocket';
import { useProjectActions } from '../hooks/useProjectActions';
import { projectReducer, initialProjectState } from '../reducers/projectReducer';
import type { ProjectMessage, AgentState as AgentStateType } from '../types';
import type { ActivityEvent } from '../api';
import {
  messagesToActivities, activityEventsToEntries,
  reconstructSdkCalls, reconstructAgentStates,
} from '../utils/activityHelpers';

// ── Extracted sub-components ──
import ApprovalModal from '../components/ApprovalModal';
import {
  ProjectErrorState,
  ProjectLoadingSkeleton,
  ResumableTaskBanner,
} from '../components/ProjectHeader';
import { ClearHistoryModal, MobileLayout, DesktopLayout } from '../components/project';

// ============================================================================
// Component
// ============================================================================

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

  // Tick counter for elapsed time displays — 10s is sufficient for
  // relative timestamps like "2m ago" and avoids re-rendering the entire
  // component tree every second (PERF-04).
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(timer);
  }, []);

  // ── Custom hooks ──
  useSmartHeartbeat(agentStates, dispatch);
  useDagPersistence(id, dagGraph, dagTaskStatus, dispatch);
  const agentMetrics = useAgentMetrics(activities);

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
        dispatch({
          type: 'LOAD_EARLIER_MESSAGES',
          messages: [],
          newOffset: messageOffset,
          hasMore: false,
        });
      }
    } catch {
      /* ignore pagination errors */
    }
  }, [id, messageOffset, hasMoreMessages]);

  // ── Initial data load ──
  useEffect(() => {
    if (!id) return;
    dispatch({ type: 'SET_LOAD_ERROR', error: null });
    loadProject().catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : 'Failed to load project';
      dispatch({ type: 'SET_LOAD_ERROR', error: msg });
    });

    Promise.all([
      getMessages(id, 100).catch(() => ({
        messages: [] as ProjectMessage[],
        total: 0,
      })),
      getActivity(id, 0, 500).catch(() => ({
        events: [] as ActivityEvent[],
        latest_sequence: 0,
        source: 'none',
      })),
    ]).then(([msgData, actData]) => {
      const msgEntries = messagesToActivities(msgData.messages);
      const actEntries = activityEventsToEntries(actData.events);
      const merged = [...msgEntries, ...actEntries].sort(
        (a, b) => a.timestamp - b.timestamp,
      );
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

    Promise.all([getResumableTask(id), getProject(id)])
      .then(([data, proj]) => {
        if (data.resumable && data.task && proj.status !== 'running') {
          dispatch({ type: 'SET_RESUMABLE_TASK', task: data.task });
        } else {
          dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
        }
      })
      .catch(() => {});

    getLiveState(id)
      .then((live) => {
        if (live.agent_states && Object.keys(live.agent_states).length > 0) {
          const restored: Record<string, AgentStateType> = {};
          for (const [name, s] of Object.entries(live.agent_states)) {
            const isWorking =
              (s.state as AgentStateType['state']) === 'working';
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
      })
      .catch(() => {});

    const statusPoll = setInterval(() => {
      loadProject().catch(() => {});
    }, 10000);
    return () => clearInterval(statusPoll);
  }, [id, loadProject, loadFiles]);

  // ── WebSocket subscription ──
  const handleWSEvent = useProjectWebSocket({
    projectId: id,
    dispatch,
    loadProject,
    loadFiles,
    toast,
  });
  const { connected } = useWSSubscribe(handleWSEvent);
  useIOSViewport();

  // ── Action handlers — must be unconditional (Rules of Hooks). ──
  // Each handler guards internally against id being undefined.
  const actions = useProjectActions(id, dispatch, toast, loadProject);

  // ── Error / Loading early returns ──
  if (loadError) {
    return (
      <ProjectErrorState
        error={loadError}
        onRetry={() => {
          dispatch({ type: 'SET_LOAD_ERROR', error: null });
          loadProject().catch((e: unknown) => {
            const msg = e instanceof Error ? e.message : 'Failed to load project';
            dispatch({ type: 'SET_LOAD_ERROR', error: msg });
          });
        }}
      />
    );
  }
  if (!project || !id) return <ProjectLoadingSkeleton />;

  // ── Computed values ──
  const agentStateList: AgentStateType[] = project.agents.map(
    (name) =>
      agentStates[name] ?? {
        name,
        state: 'idle' as const,
        cost: 0,
        turns: 0,
        duration: 0,
      },
  );
  const orchestratorState =
    agentStateList.find((a) => a.name === 'orchestrator') ?? null;
  const subAgentStates = agentStateList.filter(
    (a) => a.name !== 'orchestrator',
  );

  // ════════════════════════════════════════════════════════════════════════
  // LAYOUT
  // ════════════════════════════════════════════════════════════════════════

  return (
    <div
      className="h-full flex flex-col"
      style={{
        background: 'var(--bg-void)',
        overflow: 'hidden',
        position: 'fixed',
        inset: 0,
      }}
    >
      {/* Resume interrupted task banner */}
      {resumableTask && (
        <ResumableTaskBanner
          resumableTask={resumableTask}
          onResume={actions.handleResumeTask}
          onDiscard={actions.handleDiscardTask}
        />
      )}

      {/* Mobile Layout */}
      <MobileLayout
        project={project}
        projectId={id}
        connected={connected}
        orchestratorState={orchestratorState}
        subAgentStates={subAgentStates}
        agentStateList={agentStateList}
        agentStates={agentStates}
        loopProgress={loopProgress}
        activities={activities}
        files={files}
        sdkCalls={sdkCalls}
        liveAgentStream={liveAgentStream}
        now={now}
        lastTicker={lastTicker}
        dagGraph={dagGraph}
        dagTaskStatus={dagTaskStatus}
        mobileView={mobileView}
        hasMoreMessages={hasMoreMessages}
        message={message}
        sending={sending}
        onSetMobileView={(view) => dispatch({ type: 'SET_MOBILE_VIEW', view })}
        onLoadMore={loadEarlierMessages}
        onPause={actions.handlePause}
        onResume={actions.handleResume}
        onStop={actions.handleStop}
        onShowClearConfirm={() =>
          dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })
        }
        onMessageChange={setMessage}
        onMobileSend={actions.handleMobileSend}
      />

      {/* Desktop Layout */}
      <DesktopLayout
        project={project}
        projectId={id}
        connected={connected}
        orchestratorState={orchestratorState}
        subAgentStates={subAgentStates}
        agentStateList={agentStateList}
        agentStates={agentStates}
        loopProgress={loopProgress}
        activities={activities}
        files={files}
        sdkCalls={sdkCalls}
        liveAgentStream={liveAgentStream}
        now={now}
        lastTicker={lastTicker}
        dagGraph={dagGraph}
        dagTaskStatus={dagTaskStatus}
        healingEvents={healingEvents}
        desktopTab={desktopTab}
        selectedAgent={selectedAgent}
        hasMoreMessages={hasMoreMessages}
        message={message}
        agentMetrics={agentMetrics}
        onSetDesktopTab={(tab) => dispatch({ type: 'SET_DESKTOP_TAB', tab })}
        onSelectAgent={(agent) =>
          dispatch({ type: 'SET_SELECTED_AGENT', agent })
        }
        onLoadMore={loadEarlierMessages}
        onPause={actions.handlePause}
        onResume={actions.handleResume}
        onStop={actions.handleStop}
        onSend={actions.handleSend}
        onShowClearConfirm={() =>
          dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true })
        }
      />

      {/* Approval Modal */}
      {approvalRequest && id && (
        <ApprovalModal
          description={approvalRequest}
          projectId={id}
          onClose={() =>
            dispatch({ type: 'SET_APPROVAL_REQUEST', request: null })
          }
        />
      )}

      {/* Clear History Confirmation Modal */}
      {showClearConfirm && (
        <ClearHistoryModal
          onConfirm={actions.handleClearHistory}
          onCancel={() =>
            dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false })
          }
        />
      )}
    </div>
  );
}
