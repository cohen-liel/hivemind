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
 * All state is provided to layout children via ProjectContext (STATE-01 fix),
 * eliminating the 20+ prop drilling through layout components.
 * All rendering is delegated to extracted sub-components
 * in components/project/ and components/.
 */

import { useEffect, useReducer, useState, useCallback, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import {
  getProject, getMessages, getFiles, getLiveState, getActivity,
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
import {
  getPersistedDesktopTab,
  getPersistedMobileView,
  setPersistedDesktopTab,
  setPersistedMobileView,
} from '../hooks/useUIStatePersistence';
import type { Project, AgentState as AgentStateType } from '../types';
import type { ActivityEvent } from '../api';
import { messagesToActivities } from '../utils/activityHelpers';

// ── Extracted sub-components ──
import ApprovalModal from '../components/ApprovalModal';
import {
  ProjectErrorState,
  ProjectLoadingSkeleton,
} from '../components/ProjectHeader';
import {
  ClearHistoryModal,
  MobileLayout,
  DesktopLayout,
  ProjectContext,
} from '../components/project';
import type { ProjectContextValue } from '../components/project';
import type { DesktopTab, MobileView } from '../reducers/projectReducer';

// ============================================================================
// Component
// ============================================================================

export default function ProjectView(): React.ReactElement | null {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();

  // ── Single useReducer replaces 21 individual useState hooks ──
  // Initialize with persisted tab/view selections from localStorage
  const [state, dispatch] = useReducer(projectReducer, {
    ...initialProjectState,
    desktopTab: getPersistedDesktopTab(),
    mobileView: getPersistedMobileView(),
  });
  const {
    project, activities, agentStates, loopProgress, files, loadError,
    sdkCalls, liveAgentStream, lastTicker, dagGraph, dagTaskStatus, dagTaskFailureReasons,
    healingEvents, mobileView, desktopTab, selectedAgent, showClearConfirm,
    sending, messageOffset, hasMoreMessages, approvalRequest, pendingQuestion,
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

    // Fetch latest sequence ID for deduplication but don't load old
    // messages/activities — start with a clean feed every time.
    // The live state endpoint (below) provides current agent states,
    // DAG graph, and task statuses so the UI shows the real-time picture.
    getActivity(id, 0, 1).catch(() => ({
      events: [] as ActivityEvent[],
      latest_sequence: 0,
      source: 'none',
    })).then((actData) => {
      dispatch({
        type: 'LOAD_INITIAL_DATA',
        activities: [],
        sdkCalls: [],
        agentStates: {},
        dagTaskStatus: {},
        hasMoreMessages: false,
        messageOffset: 0,
        lastSequenceId: actData.latest_sequence ?? 0,
      });
    });

    loadFiles().catch((e: unknown) => console.error('[ProjectView] loadFiles failed:', e));

    // Initial live state load — single dispatch handles all fields
    getLiveState(id)
      .then((live) => {
        dispatch({
          type: 'WS_LIVE_STATE_SYNC',
          event: {
            type: 'live_state_sync',
            project_id: id,
            agent_states: live.agent_states,
            // LiveState uses null for "not set"; WSEvent uses undefined
            loop_progress: live.loop_progress ?? undefined,
            dag_graph: live.dag_graph ?? undefined,
            dag_task_statuses: live.dag_task_statuses,
            status: live.status,
            timestamp: Date.now() / 1000,
          },
        });
        if (live.pending_approval) {
          dispatch({ type: 'SET_APPROVAL_REQUEST', request: live.pending_approval });
        }
      })
      .catch((e: unknown) => console.error('[ProjectView] getLiveState failed:', e));

    // Poll project status AND live agent states every 5 seconds.
    // This ensures the UI stays up-to-date even when WebSocket is disconnected
    // (critical for mobile/iOS where WS connections are unreliable).
    const statusPoll = setInterval(async () => {
      try {
        const p = await getProject(id);
        dispatch({ type: 'SET_PROJECT', project: p });
        if (p.agent_states && Object.keys(p.agent_states).length > 0) {
          dispatch({ type: 'MERGE_AGENT_STATES_FROM_POLL', agentStates: p.agent_states });
        }
        // If project is running, also fetch detailed live state for agent progress.
        // Use a single WS_LIVE_STATE_SYNC dispatch which handles all fields:
        // agent_states, loop_progress, dag_graph, dag_task_statuses.
        if (p.is_running || p.status === 'running') {
          try {
            const live = await getLiveState(id);
            dispatch({
              type: 'WS_LIVE_STATE_SYNC',
              event: {
                type: 'live_state_sync',
                project_id: id,
                agent_states: live.agent_states,
                // LiveState uses null for "not set"; WSEvent uses undefined
                loop_progress: live.loop_progress ?? undefined,
                dag_graph: live.dag_graph ?? undefined,
                dag_task_statuses: live.dag_task_statuses,
                status: live.status,
                timestamp: Date.now() / 1000,
              },
            });
          } catch {
            // Live state fetch failed — non-critical
          }
        }
      } catch (e: unknown) {
        console.error('[ProjectView] status poll failed:', e);
      }
    }, 5000);
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

  // Stable callback references — dispatch identity never changes so these
  // are created once and never cause downstream re-renders.
  // Tab/view changes are also persisted to localStorage for cross-reload retention.
  const onSetDesktopTab = useCallback(
    (tab: DesktopTab) => {
      dispatch({ type: 'SET_DESKTOP_TAB', tab });
      setPersistedDesktopTab(tab);
    }, [],
  );
  const onSelectAgent = useCallback(
    (agent: string | null) => dispatch({ type: 'SET_SELECTED_AGENT', agent }), [],
  );
  const onSetMobileView = useCallback(
    (view: MobileView) => {
      dispatch({ type: 'SET_MOBILE_VIEW', view });
      setPersistedMobileView(view);
    }, [],
  );
  const onShowClearConfirm = useCallback(
    () => dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: true }), [],
  );
  const onClearQuestion = useCallback(
    () => dispatch({ type: 'CLEAR_PRE_TASK_QUESTION' }), [],
  );

  // ════════════════════════════════════════════════════════════════════════
  // CONTEXT VALUE — must be above early returns (Rules of Hooks: useMemo
  // must run on every render, even when we return early below).
  // ════════════════════════════════════════════════════════════════════════

  // The useMemo hook must run unconditionally (before early returns) to satisfy
  // Rules of Hooks.  The value is only consumed via Provider after the guards
  // below, so project/id will always be defined when consumers read the context.
  const contextValue = useMemo((): ProjectContextValue => {
    // Computed agent lists — only meaningful when project is loaded
    const agentList: AgentStateType[] = (project?.agents || []).map(
      (name) =>
        agentStates[name] ?? {
          name,
          state: 'idle' as const,
          cost: 0,
          turns: 0,
          duration: 0,
        },
    );
    const orch = agentList.find((a) => a.name === 'orchestrator') ?? null;
    const subs = agentList.filter((a) => a.name !== 'orchestrator');

    return {
      // Core data — non-null assertion safe: Provider only renders after
      // the early-return guards that check project !== null && id !== undefined.
      project: project as Project,
      projectId: id as string,
      connected,

      // Agent state
      orchestratorState: orch,
      subAgentStates: subs,
      agentStateList: agentList,
      agentStates,
      loopProgress,

      // Activity & content
      activities,
      files,
      sdkCalls,
      liveAgentStream,
      agentMetrics,

      // Time & display
      now,
      lastTicker,

      // DAG
      dagGraph,
      dagTaskStatus,
      dagTaskFailureReasons,
      healingEvents,

      // UI view state
      desktopTab,
      selectedAgent,
      mobileView,

      // Messaging
      hasMoreMessages,
      message,
      sending,

      // Callbacks (stable refs)
      onSetDesktopTab,
      onSelectAgent,
      onSetMobileView,
      onLoadMore: loadEarlierMessages,
      onPause: actions.handlePause,
      onResume: actions.handleResume,
      onStop: actions.handleStop,
      onSend: actions.handleSend,
      onMobileSend: actions.handleMobileSend,
      onShowClearConfirm,
      onMessageChange: setMessage,
      pendingQuestion,
      onClearQuestion,
    };
  }, [
    project, id, connected,
    agentStates, loopProgress,
    activities, files, sdkCalls, liveAgentStream, agentMetrics,
    now, lastTicker,
    dagGraph, dagTaskStatus, dagTaskFailureReasons, healingEvents,
    desktopTab, selectedAgent, mobileView,
    hasMoreMessages, message, sending,
    onSetDesktopTab, onSelectAgent, onSetMobileView,
    loadEarlierMessages, actions, onShowClearConfirm, setMessage,
    pendingQuestion, onClearQuestion,
  ]);

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

  // ════════════════════════════════════════════════════════════════════════
  // LAYOUT — ProjectContext eliminates prop drilling to layout components
  // ════════════════════════════════════════════════════════════════════════

  return (
    <ProjectContext.Provider value={contextValue}>
      <div
        className="h-full flex flex-col"
        style={{
          background: 'var(--bg-void)',
          overflow: 'hidden',
          position: 'fixed',
          inset: 0,
        }}
      >
        {/* Mobile Layout */}
        <MobileLayout />

        {/* Desktop Layout */}
        <DesktopLayout />

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
    </ProjectContext.Provider>
  );
}
