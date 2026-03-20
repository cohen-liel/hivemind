/**
 * MobileLayout — Mobile layout composition for ProjectView.
 *
 * Renders the mobile-optimized layout with tab-switched content panels,
 * including error boundaries around each panel for resilience.
 *
 * STATE-01 fix: Consumes ProjectContext instead of receiving 20+ props.
 */

import React from 'react';
import { useProjectContext } from './ProjectContext';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import { useScrollPersistence } from '../../hooks/useUIStatePersistence';

// ── Existing components ──
import ConductorBar from '../ConductorBar';
import ConductorMode from '../ConductorMode';
import PipelinePhases from '../PipelinePhases';
import PlanView from '../PlanView';
import ActivityFeed from '../ActivityFeed';
import MobileTabNav from '../MobileTabNav';
import { MobileLiveAgentStream } from '../ActivityPanel';
import CodePanel from '../CodePanel';
import ChangesPanel from '../ChangesPanel';
import TracePanel from '../TracePanel';
import SessionSummary from '../SessionSummary';

// ============================================================================
// Component
// ============================================================================

const MobileLayout = React.memo(function MobileLayout(): React.ReactElement {
  const {
    project, projectId, connected, orchestratorState, subAgentStates,
    agentStateList, agentStates, loopProgress, activities, files, sdkCalls,
    liveAgentStream, now, lastTicker, dagGraph, dagTaskStatus, dagTaskFailureReasons,
    mobileView, hasMoreMessages, message, sending,
    onSetMobileView, onLoadMore, onPause, onResume, onStop,
    onShowClearConfirm, onMessageChange, onMobileSend,
  } = useProjectContext();

  // ── Scroll persistence for the mobile content area ──
  const mobileScrollRef = useScrollPersistence(`mobile-${mobileView}`, connected);

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
        paddingLeft: 'env(safe-area-inset-left, 0px)',
        paddingRight: 'env(safe-area-inset-right, 0px)',
        touchAction: 'pan-y',
      }}
    >
      <ConductorBar
        projectId={project.project_id}
        projectName={project.project_name}
        status={project.status}
        connected={connected}
        orchestrator={orchestratorState}
        progress={loopProgress}
        agentSummary={subAgentStates}
        lastTicker={lastTicker}
      />

      <PipelinePhases
        orchestrator={orchestratorState}
        status={project.status}
        now={now}
      />

      <div
        ref={mobileScrollRef}
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
                  processing={sending}
                />
              </div>
              <SessionSummary projectId={projectId} projectStatus={project.status} />
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
              dagTaskFailureReasons={dagTaskFailureReasons}
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
