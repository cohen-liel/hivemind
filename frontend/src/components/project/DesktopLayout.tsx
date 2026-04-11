/**
 * DesktopLayout — Desktop layout composition for ProjectView.
 *
 * Renders the desktop-optimized split layout with tab bar, live status strip,
 * tab content on the left, and activity panel on the right.
 * Each tab panel is wrapped in an error boundary for resilience.
 *
 * Features:
 * - Resizable split panel (drag divider) with localStorage persistence
 * - Scroll position persistence across reconnections
 *
 * STATE-01 fix: Consumes ProjectContext instead of receiving 20+ props.
 */

import React, { useRef } from 'react';
import { Link } from 'react-router-dom';
import { useProjectContext } from './ProjectContext';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import {
  useResizablePanel,
  useScrollPersistence,
} from '../../hooks/useUIStatePersistence';

// ── Existing components ──
import ConductorBar from '../ConductorBar';
import PipelinePhases from '../PipelinePhases';
import PlanView from '../PlanView';
import {
  LiveStatusStrip,
  DesktopTabBar,
  HivemindTabContent,
} from '../AgentOrchestra';
import ActivityPanel from '../ActivityPanel';
import CodePanel from '../CodePanel';
import ChangesPanel from '../ChangesPanel';
import TracePanel from '../TracePanel';

// ============================================================================
// Component
// ============================================================================

const DesktopLayout = React.memo(function DesktopLayout(): React.ReactElement {
  const {
    project, projectId, connected, orchestratorState, subAgentStates,
    agentStateList, agentStates, loopProgress, activities, files, sdkCalls,
    liveAgentStream, now, lastTicker, dagGraph, dagTaskStatus, dagTaskFailureReasons, healingEvents,
    desktopTab, selectedAgent, hasMoreMessages, message, agentMetrics,
    onSetDesktopTab, onSelectAgent, onLoadMore, onPause, onResume, onStop,
    onSend, onShowClearConfirm,
  } = useProjectContext();

  // ── Resizable split panel ──
  const splitContainerRef = useRef<HTMLDivElement | null>(null);
  const { panelWidth, isDragging, dragHandleProps } = useResizablePanel(splitContainerRef);

  // ── Scroll persistence for the left content panel ──
  const leftScrollRef = useScrollPersistence(`desktop-left-${desktopTab}`, connected);

  return (
    <div className="hidden lg:flex flex-col h-full w-full overflow-hidden">
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

      {/* View DAG link — positioned between ConductorBar and PipelinePhases */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          padding: '4px 12px',
          background: 'var(--bg-panel)',
          borderBottom: '1px solid var(--border-dim)',
        }}
      >
        <Link
          to={`/projects/${projectId}/dag`}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '4px 10px',
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 500,
            color: 'var(--accent-blue)',
            background: 'rgba(99,140,255,0.08)',
            border: '1px solid rgba(99,140,255,0.2)',
            textDecoration: 'none',
          }}
          aria-label="View DAG visualization"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="3" cy="8" r="2"/>
            <circle cx="13" cy="3" r="2"/>
            <circle cx="13" cy="13" r="2"/>
            <path d="M5 8h3l2-3M5 8h3l2 3"/>
          </svg>
          View DAG
        </Link>
      </div>

      <PipelinePhases
        orchestrator={orchestratorState}
        status={project.status}
        now={now}
      />

      <DesktopTabBar
        desktopTab={desktopTab}
        onSetDesktopTab={onSetDesktopTab}
        projectStatus={project.status}
        activitiesCount={activities.length}
        onShowClearConfirm={onShowClearConfirm}
      />

      <LiveStatusStrip
        orchestratorState={orchestratorState}
        subAgentStates={subAgentStates}
        now={now}
        lastTicker={lastTicker}
      />

      {/* Split view: tab content (left) + drag handle + activity log (right) */}
      <div
        ref={splitContainerRef}
        className="flex-1 flex min-h-0 overflow-hidden"
        style={{
          width: '100%',
          // Prevent text selection while dragging the divider
          userSelect: isDragging ? 'none' : undefined,
        }}
      >
        {/* Left panel — tab content */}
        <div
          ref={leftScrollRef}
          className="overflow-y-auto overflow-x-hidden min-w-0"
          style={{
            width: `${panelWidth}%`,
            maxWidth: `${panelWidth}%`,
            flexShrink: 0,
          }}
        >
          {desktopTab === 'hivemind' && (
            <PanelErrorBoundary panelName="Hivemind">
              <HivemindTabContent
                agentStateList={agentStateList}
                loopProgress={loopProgress}
                activities={activities}
                projectStatus={project.status}
                messageDraft={message}
                healingEvents={healingEvents}
                selectedAgent={selectedAgent}
                onSelectAgent={onSelectAgent}
                agentMetrics={agentMetrics}
              />
            </PanelErrorBoundary>
          )}
          {desktopTab === 'plan' && (
            <PanelErrorBoundary panelName="Plan">
              <PlanView
                activities={activities}
                dagGraph={dagGraph}
                dagTaskStatus={dagTaskStatus}
                dagTaskFailureReasons={dagTaskFailureReasons}
                projectStatus={project.status}
                orchestratorTask={orchestratorState?.current_tool || orchestratorState?.task}
              />
            </PanelErrorBoundary>
          )}
          {desktopTab === 'code' && (
            <PanelErrorBoundary panelName="Code">
              <CodePanel projectId={projectId} />
            </PanelErrorBoundary>
          )}
          {desktopTab === 'diff' && (
            <PanelErrorBoundary panelName="Diff">
              <ChangesPanel files={files} variant="desktop" />
            </PanelErrorBoundary>
          )}
          {desktopTab === 'trace' && (
            <PanelErrorBoundary panelName="Trace">
              <TracePanel calls={sdkCalls} variant="desktop" />
            </PanelErrorBoundary>
          )}
        </div>

        {/* ── Drag handle / divider ── */}
        <div
          {...dragHandleProps}
          className="group relative flex-shrink-0"
          style={{
            ...dragHandleProps.style,
            width: '8px',
          }}
        >
          {/* Visual indicator line */}
          <div
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: '3px',
              width: '2px',
              background: isDragging
                ? 'var(--accent-blue, #6366f1)'
                : 'var(--border-dim, #27272a)',
              transition: isDragging ? 'none' : 'background 0.15s ease',
              borderRadius: '1px',
            }}
          />
          {/* Hover hit area — visible dots on hover */}
          <div
            aria-hidden="true"
            className="opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              display: 'flex',
              flexDirection: 'column',
              gap: '3px',
              transition: 'opacity 0.15s ease',
            }}
          >
            {[0, 1, 2].map(i => (
              <div
                key={i}
                style={{
                  width: '3px',
                  height: '3px',
                  borderRadius: '50%',
                  background: isDragging
                    ? 'var(--accent-blue, #6366f1)'
                    : 'var(--text-muted, #71717a)',
                }}
              />
            ))}
          </div>
        </div>

        {/* Right panel — activity */}
        <PanelErrorBoundary panelName="Activity">
          <ActivityPanel
            projectId={projectId}
            agentStates={agentStates}
            liveAgentStream={liveAgentStream}
            now={now}
            activities={activities}
            hasMoreMessages={hasMoreMessages}
            onLoadMore={onLoadMore}
            projectStatus={project.status}
            onPause={onPause}
            onResume={onResume}
            onStop={onStop}
            onSend={onSend}
          />
        </PanelErrorBoundary>
      </div>
    </div>
  );
});

export default DesktopLayout;
