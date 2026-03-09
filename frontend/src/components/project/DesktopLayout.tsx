/**
 * DesktopLayout — Desktop layout composition for ProjectView.
 *
 * Renders the desktop-optimized split layout with tab bar, live status strip,
 * tab content on the left, and activity panel on the right.
 * Each tab panel is wrapped in an error boundary for resilience.
 */

import React from 'react';
import type {
  Project,
  ActivityEntry,
  AgentState,
  LoopProgress,
  FileChanges,
  WSEvent,
} from '../../types';
import type {
  SdkCall,
  DesktopTab,
  HealingEvent,
  LiveAgentEntry,
} from '../../reducers/projectReducer';
import type { AgentMetric } from '../../hooks/useAgentMetrics';
import { PanelErrorBoundary } from './PanelErrorBoundary';

// ── Existing components ──
import ConductorBar from '../ConductorBar';
import PlanView from '../PlanView';
import {
  LiveStatusStrip,
  DesktopTabBar,
  NexusTabContent,
  AgentsTabContent,
} from '../AgentOrchestra';
import ActivityPanel from '../ActivityPanel';
import CodePanel from '../CodePanel';
import ChangesPanel from '../ChangesPanel';
import TracePanel from '../TracePanel';

// ============================================================================
// Props Interface
// ============================================================================

export interface DesktopLayoutProps {
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
  /** Self-healing events */
  healingEvents: HealingEvent[];
  /** Currently active desktop tab */
  desktopTab: DesktopTab;
  /** Currently selected agent name (for agents tab) */
  selectedAgent: string | null;
  /** Whether there are more messages to load */
  hasMoreMessages: boolean;
  /** Current message draft */
  message: string;
  /** Per-agent performance metrics */
  agentMetrics: AgentMetric[];

  // ── Callbacks ──
  onSetDesktopTab: (tab: DesktopTab) => void;
  onSelectAgent: (agent: string | null) => void;
  onLoadMore: () => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onStop: () => Promise<void>;
  onSend: (msg: string) => Promise<void>;
  onShowClearConfirm: () => void;
}

// ============================================================================
// Component
// ============================================================================

const DesktopLayout = React.memo(function DesktopLayout(
  props: DesktopLayoutProps,
): React.ReactElement {
  const {
    project, projectId, connected, orchestratorState, subAgentStates,
    agentStateList, agentStates, loopProgress, activities, files, sdkCalls,
    liveAgentStream, now, lastTicker, dagGraph, dagTaskStatus, healingEvents,
    desktopTab, selectedAgent, hasMoreMessages, message, agentMetrics,
    onSetDesktopTab, onSelectAgent, onLoadMore, onPause, onResume, onStop,
    onSend, onShowClearConfirm,
  } = props;

  return (
    <div className="hidden lg:flex flex-col h-full w-full overflow-hidden">
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

      {/* Split view: tab content (left) + activity log (right) */}
      <div
        className="flex-1 flex min-h-0 overflow-hidden"
        style={{ width: '100%' }}
      >
        <div
          className="overflow-y-auto overflow-x-hidden min-w-0"
          style={{ width: '65%', maxWidth: '65%', flexShrink: 0 }}
        >
          {desktopTab === 'nexus' && (
            <PanelErrorBoundary panelName="Nexus">
              <NexusTabContent
                agentStateList={agentStateList}
                loopProgress={loopProgress}
                activities={activities}
                totalCost={project.total_cost_usd}
                projectStatus={project.status}
                messageDraft={message}
                dagGraph={dagGraph}
                dagTaskStatus={dagTaskStatus}
                healingEvents={healingEvents}
              />
            </PanelErrorBoundary>
          )}
          {desktopTab === 'agents' && (
            <PanelErrorBoundary panelName="Agents">
              <AgentsTabContent
                agentStateList={agentStateList}
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

        <PanelErrorBoundary panelName="Activity">
          <ActivityPanel
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
