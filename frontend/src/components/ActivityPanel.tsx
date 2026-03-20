import React from 'react';
import type { AgentState as AgentStateType, ActivityEntry } from '../types';
import type { LiveAgentEntry } from '../reducers/projectReducer';
import ActivityFeed from './ActivityFeed';
import Controls from './Controls';
import SessionSummary from './SessionSummary';
import PreTaskQuestion from './PreTaskQuestion';
import { useProjectContext } from './project/ProjectContext';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

// ============================================================================
// Props Interfaces
// ============================================================================

export interface ActivityPanelProps {
  projectId: string;
  agentStates: Record<string, AgentStateType>;
  liveAgentStream: Record<string, LiveAgentEntry>;
  now: number;
  activities: ActivityEntry[];
  hasMoreMessages: boolean;
  onLoadMore: () => void;
  projectStatus: string;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onSend: (msg: string, mode?: string) => Promise<void>;
}

interface LiveAgentStreamProps {
  agentStates: Record<string, AgentStateType>;
  liveAgentStream: Record<string, LiveAgentEntry>;
  now: number;
  /** Maximum height of the scrollable container. Default: 240 */
  maxHeight?: number;
  /** Max lines of agent text to show via -webkit-line-clamp. Default: 3 */
  maxTextLines?: number;
  /** Whether to show the progress sub-line. Default: true */
  showProgress?: boolean;
}

// ============================================================================
// LiveAgentStream — unified component for desktop + mobile live agent display
// ============================================================================

export const LiveAgentStream = React.memo(function LiveAgentStream({
  agentStates,
  liveAgentStream,
  now,
  maxHeight = 240,
  maxTextLines = 3,
  showProgress = true,
}: LiveAgentStreamProps): React.ReactElement | null {
  // Use agentStates as source of truth: ALL working agents, with liveAgentStream data overlaid
  const activeAgents = Object.entries(agentStates)
    .filter(([, a]) => a.state === 'working' || a.state === 'waiting')
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
    <div
      className="flex-shrink-0 overflow-hidden"
      style={{
        borderBottom: '1px solid var(--border-dim)',
        background: 'var(--bg-elevated)',
        maxHeight: `${maxHeight}px`,
        overflowY: 'auto',
      }}
    >
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
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded font-mono font-medium flex-shrink-0"
                  style={{ background: `${ac.color}18`, color: ac.color, border: `1px solid ${ac.color}30` }}
                >
                  {entry.tool}
                </span>
              )}
              {elapsedSec > 0 && (
                <span className="text-[10px] ml-auto font-mono flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
                  {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
                </span>
              )}
            </div>

            {/* Current thought / action */}
            {entry.text && (
              <p
                className="text-[11px] leading-relaxed pl-3.5"
                style={{
                  color: 'var(--text-secondary)',
                  fontFamily: 'var(--font-mono)',
                  wordBreak: 'break-word',
                  display: '-webkit-box',
                  WebkitLineClamp: maxTextLines,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {entry.text}
              </p>
            )}

            {/* Progress sub-line (desktop only by default) */}
            {showProgress && entry.progress && (
              <span className="text-[10px] pl-3.5 mt-0.5 block font-mono" style={{ color: 'var(--text-muted)' }}>
                {entry.progress}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
});

/** Convenience alias for the mobile live stream panel (compact height, fewer text lines, no progress). */
export const MobileLiveAgentStream = React.memo(function MobileLiveAgentStream(
  props: Omit<LiveAgentStreamProps, 'maxHeight' | 'maxTextLines' | 'showProgress'>
): React.ReactElement | null {
  return <LiveAgentStream {...props} maxHeight={200} maxTextLines={2} showProgress={false} />;
});

// ============================================================================
// ActivityPanel — Desktop right sidebar with live stream + feed + controls
// ============================================================================

/** Desktop right-side panel showing live agent stream, activity feed, and chat controls. */
const ActivityPanel = React.memo(function ActivityPanel({
  projectId,
  agentStates,
  liveAgentStream,
  now,
  activities,
  hasMoreMessages,
  onLoadMore,
  projectStatus,
  onPause,
  onResume,
  onStop,
  onSend,
}: ActivityPanelProps): React.ReactElement {
  const workingCount = Object.values(agentStates).filter(a => a.state === 'working' || a.state === 'waiting').length;
  const { pendingQuestion, onClearQuestion } = useProjectContext();

  return (
    <div
      className="flex flex-col min-w-0 overflow-hidden"
      style={{ width: '35%', maxWidth: '35%', flexShrink: 0, borderLeft: '1px solid var(--border-dim)', background: 'var(--bg-panel)' }}
    >
      {/* Header */}
      <div
        className="px-4 py-2 flex items-center justify-between flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)', zIndex: 10 }}
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Activity Log
        </h3>
        {workingCount > 0 && (
          <div className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
            <span className="text-[10px] font-mono" style={{ color: 'var(--accent-green)' }}>
              {workingCount} running
            </span>
          </div>
        )}
      </div>

      {/* Live Agent Stream — desktop defaults (240px height, 3 text lines, show progress) */}
      <LiveAgentStream agentStates={agentStates} liveAgentStream={liveAgentStream} now={now} />

      <div className="flex-1 overflow-y-auto min-h-0" id="activity-scroll-container">
        <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={onLoadMore} />
        {/* Session summary — inside scroll area so long summaries don't eat fixed space */}
        <SessionSummary projectId={projectId} projectStatus={projectStatus} />
      </div>

      {/* Pre-task question — shown when orchestrator needs clarification */}
      {pendingQuestion && (
        <PreTaskQuestion
          question={pendingQuestion}
          onSend={(answer) => { onClearQuestion(); void onSend(answer); }}
          onDismiss={onClearQuestion}
        />
      )}

      {/* Chat input — anchored to bottom of activity panel */}
      <Controls
        status={projectStatus}
        onPause={onPause}
        onResume={onResume}
        onStop={onStop}
        onSend={onSend}
      />
    </div>
  );
});

export default ActivityPanel;
