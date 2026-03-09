/**
 * MobileLayout — Mobile layout composition for ProjectView.
 *
 * Renders the mobile-optimized layout with tab-switched content panels,
 * including error boundaries around each panel for resilience.
 */

import React from 'react';
import type { Project, ActivityEntry, AgentState, LoopProgress, FileChanges, WSEvent } from '../../types';
import type { SdkCall, MobileView, LiveAgentEntry } from '../../reducers/projectReducer';
import { PanelErrorBoundary } from './PanelErrorBoundary';

// ── Existing components ──
import ConductorBar from '../ConductorBar';
import ConductorMode from '../ConductorMode';
import PlanView from '../PlanView';
import ActivityFeed from '../ActivityFeed';
import MobileTabNav from '../MobileTabNav';
import { MobileLiveAgentStream } from '../ActivityPanel';
import CodePanel from '../CodePanel';
import ChangesPanel from '../ChangesPanel';
import TracePanel from '../TracePanel';

// ============================================================================
// Props Interface
// ============================================================================

export interface MobileLayoutProps {
  /** Project data */
  project: Project;
  /** Current project ID */
  projectId: string;
  /** WebSocket connection status */
  connected: boolean;
  /** Orchestrator agent state (or null) */
  orchestratorState: AgentState | null;
  /** Sub-agent states (excludes orchestrator) */
  subAgentStates: AgentState[];
  /** All agent states as a list */
  agentStateList: AgentState[];
  /** Agent states keyed by name */
  agentStates: Record<string, AgentState>;
  /** Loop progress info */
  loopProgress: LoopProgress | null;
  /** Activity entries for the feed */
  activities: ActivityEntry[];
  /** File changes data */
  files: FileChanges | null;
  /** SDK call traces */
  sdkCalls: SdkCall[];
  /** Live agent stream data */
  liveAgentStream: Record<string, LiveAgentEntry>;
  /** Current timestamp for elapsed displays */
  now: number;
  /** Last ticker message */
  lastTicker: string;
  /** DAG graph data */
  dagGraph: WSEvent['graph'] | null;
  /** DAG task status map */
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed'>;
  /** Currently active mobile view tab */
  mobileView: MobileView;
  /** Whether there are more messages to load */
  hasMoreMessages: boolean;
  /** Current message draft */
  message: string;
  /** Whether a message is being sent */
  sending: boolean;

  // ── Callbacks ──
  onSetMobileView: (view: MobileView) => void;
  onLoadMore: () => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onStop: () => Promise<void>;
  onShowClearConfirm: () => void;
  onMessageChange: (msg: string) => void;
  onMobileSend: (msg: string) => void;
}

// ============================================================================
// Component
// ============================================================================

const MobileLayout = React.memo(function MobileLayout(
  props: MobileLayoutProps,
): React.ReactElement {
  const {
    project, projectId, connected, orchestratorState, subAgentStates,
    agentStateList, agentStates, loopProgress, activities, files, sdkCalls,
    liveAgentStream, now, lastTicker, dagGraph, dagTaskStatus,
    mobileView, hasMoreMessages, message, sending,
    onSetMobileView, onLoadMore, onPause, onResume, onStop,
    onShowClearConfirm, onMessageChange, onMobileSend,
  } = props;

  return (
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
      <ConductorBar
        projectName={project.project_name}
        status={project.status}
        connected={connected}
        orchestrator={orchestratorState}
        progress={loopProgress}
        totalCost={project.total_cost_usd}
        agentSummary={subAgentStates}
        lastTicker={lastTicker}
      />

      <div
        className="flex-1 overflow-y-auto min-h-0"
        style={{
          overscrollBehavior: 'none',
          touchAction: 'pan-y',
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {mobileView === 'orchestra' && (
          <PanelErrorBoundary panelName="Orchestra">
            <ConductorMode
              agents={agentStateList}
              progress={loopProgress}
              activities={activities}
              totalCost={project.total_cost_usd}
              status={project.status}
              messageDraft={message}
            />
          </PanelErrorBoundary>
        )}
        {mobileView === 'activity' && (
          <PanelErrorBoundary panelName="Activity">
            <div className="flex flex-col h-full">
              <MobileLiveAgentStream
                agentStates={agentStates}
                liveAgentStream={liveAgentStream}
                now={now}
              />
              <div className="flex-1 min-h-0 overflow-hidden">
                <ActivityFeed
                  activities={activities}
                  hasMore={hasMoreMessages}
                  onLoadMore={onLoadMore}
                />
              </div>
            </div>
          </PanelErrorBoundary>
        )}
        {mobileView === 'code' && (
          <PanelErrorBoundary panelName="Code">
            <CodePanel projectId={projectId} />
          </PanelErrorBoundary>
        )}
        {mobileView === 'changes' && (
          <PanelErrorBoundary panelName="Changes">
            <ChangesPanel files={files} variant="mobile" />
          </PanelErrorBoundary>
        )}
        {mobileView === 'plan' && (
          <PanelErrorBoundary panelName="Plan">
            <PlanView
              activities={activities}
              dagGraph={dagGraph}
              dagTaskStatus={dagTaskStatus}
            />
          </PanelErrorBoundary>
        )}
        {mobileView === 'trace' && (
          <PanelErrorBoundary panelName="Trace">
            <TracePanel calls={sdkCalls} variant="mobile" />
          </PanelErrorBoundary>
        )}
      </div>

      <MobileTabNav
        mobileView={mobileView}
        onSetMobileView={onSetMobileView}
        projectStatus={project.status}
        activitiesCount={activities.length}
        onPause={onPause}
        onResume={onResume}
        onStop={onStop}
        onShowClearConfirm={onShowClearConfirm}
        lastTicker={lastTicker}
        message={message}
        onMessageChange={onMessageChange}
        sending={sending}
        onSend={onMobileSend}
      />
    </div>
  );
});

export default MobileLayout;
