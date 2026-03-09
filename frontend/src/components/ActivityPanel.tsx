import React from 'react';
import type { AgentState as AgentStateType, ActivityEntry } from '../types';
import type { LiveAgentEntry } from '../reducers/projectReducer';
import ActivityFeed from './ActivityFeed';
import Controls from './Controls';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

// ============================================================================
// Props Interface
// ============================================================================

export interface ActivityPanelProps {
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
  onSend: (msg: string) => Promise<void>;
}

// ============================================================================
// LiveAgentStream — Shows real-time text from each working agent
// ============================================================================

interface LiveAgentStreamSectionProps {
  agentStates: Record<string, AgentStateType>;
  liveAgentStream: Record<string, LiveAgentEntry>;
  now: number;
}

const LiveAgentStreamSection = React.memo(function LiveAgentStreamSection({
  agentStates,
  liveAgentStream,
  now,
}: LiveAgentStreamSectionProps): React.ReactElement | null {
  // Use agentStates as source of truth: ALL working agents, with liveAgentStream data overlaid
  const activeAgents = Object.entries(agentStates)
    .filter(([, a]) => a.state === 'working')
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
    <div className="flex-shrink-0 overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-elevated)', maxHeight: '240px', overflowY: 'auto' }}>
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
});

// ============================================================================
// ActivityPanel — Desktop right sidebar with live stream + feed + controls
// ============================================================================

/** Desktop right-side panel showing live agent stream, activity feed, and chat controls. */
const ActivityPanel = React.memo(function ActivityPanel({
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
  const workingCount = Object.values(agentStates).filter(a => a.state === 'working').length;

  return (
    <div className="flex flex-col min-w-0 overflow-hidden" style={{ width: '35%', maxWidth: '35%', flexShrink: 0, borderLeft: '1px solid var(--border-dim)', background: 'var(--bg-panel)' }}>
      {/* Header */}
      <div className="px-4 py-2 flex items-center justify-between flex-shrink-0" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)', zIndex: 10 }}>
        <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Activity Log</h3>
        {workingCount > 0 && (
          <div className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
            <span className="text-[10px] font-mono" style={{ color: 'var(--accent-green)' }}>
              {workingCount} running
            </span>
          </div>
        )}
      </div>

      {/* Live Agent Stream */}
      <LiveAgentStreamSection
        agentStates={agentStates}
        liveAgentStream={liveAgentStream}
        now={now}
      />

      <div className="flex-1 overflow-y-auto min-h-0">
        <ActivityFeed activities={activities} hasMore={hasMoreMessages} onLoadMore={onLoadMore} />
      </div>
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

/** Mobile-specific live agent stream (slightly different max-height than desktop). */
export const MobileLiveAgentStream = React.memo(function MobileLiveAgentStream({
  agentStates,
  liveAgentStream,
  now,
}: LiveAgentStreamSectionProps): React.ReactElement | null {
  const activeAgents = Object.entries(agentStates)
    .filter(([, a]) => a.state === 'working')
    .map(([name, agentState]) => ({
      name,
      entry: liveAgentStream[name] ?? { text: agentState.task || 'working...', timestamp: agentState.started_at ?? now },
      agentState,
    }));
  if (activeAgents.length === 0) return null;
  return (
    <div className="flex-shrink-0 overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-elevated)', maxHeight: '200px', overflowY: 'auto' }}>
      <div className="px-3 pt-2 pb-1 flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
        <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>
          ⚡ Live — {activeAgents.length} agent{activeAgents.length > 1 ? 's' : ''} working
        </span>
      </div>
      {activeAgents.map(({ name: agentName, entry, agentState }) => {
        const ac = getAgentAccent(agentName);
        const elapsedSec = agentState.started_at ? Math.round((now - agentState.started_at) / 1000) : 0;
        return (
          <div key={agentName} className="px-3 pb-2.5 pt-1" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
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
                  {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
                </span>
              )}
            </div>
            {entry.text && (
              <p className="text-[11px] leading-relaxed pl-3.5" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                {entry.text}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
});

export default ActivityPanel;
