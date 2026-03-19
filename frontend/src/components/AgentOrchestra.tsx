import React from 'react';
import type { AgentState as AgentStateType, LoopProgress, ActivityEntry } from '../types';
import type { HealingEvent, DesktopTab } from '../reducers/projectReducer';
import type { AgentMetric } from '../hooks/useAgentMetrics';
import AgentStatusPanel from './AgentStatusPanel';
import AgentMetrics from './AgentMetrics';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

// ============================================================================
// Props Interfaces
// ============================================================================

export interface LiveStatusStripProps {
  orchestratorState: AgentStateType | null;
  subAgentStates: AgentStateType[];
  now: number;
  lastTicker: string;
}

export interface HivemindTabContentProps {
  agentStateList: AgentStateType[];
  loopProgress: LoopProgress | null;
  activities: ActivityEntry[];
  projectStatus: string;
  messageDraft: string;
  healingEvents: HealingEvent[];
  selectedAgent: string | null;
  onSelectAgent: (agent: string | null) => void;
  agentMetrics: AgentMetric[];
}

export interface DesktopTabBarProps {
  desktopTab: DesktopTab;
  onSetDesktopTab: (tab: DesktopTab) => void;
  projectStatus: string;
  activitiesCount: number;
  onShowClearConfirm: () => void;
}

// ============================================================================
// Static tab definitions
// ============================================================================

interface DesktopTabItem {
  id: DesktopTab;
  icon: React.ReactElement;
  label: string;
}

const DESKTOP_TAB_ITEMS: DesktopTabItem[] = [
  {
    id: 'hivemind',
    label: 'Hivemind',
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="4" /><line x1="12" y1="2" x2="12" y2="6" /><line x1="12" y1="18" x2="12" y2="22" /><line x1="2" y1="12" x2="6" y2="12" /><line x1="18" y1="12" x2="22" y2="12" /></svg>,
  },
  {
    id: 'plan',
    label: 'Plan',
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" /></svg>,
  },
  {
    id: 'code',
    label: 'Code',
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>,
  },
  {
    id: 'diff',
    label: 'Diff',
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 3v18M3 12h18" /></svg>,
  },
  {
    id: 'trace',
    label: 'Trace',
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></svg>,
  },
];

// ============================================================================
// LiveStatusStrip — Shows working agents as chips across the top
// ============================================================================

export const LiveStatusStrip = React.memo(function LiveStatusStrip({
  orchestratorState,
  subAgentStates,
  now,
  lastTicker,
}: LiveStatusStripProps): React.ReactElement | null {
  const workingAgents = subAgentStates.filter(a => a.state === 'working');
  const doneAgents = subAgentStates.filter(a => a.state === 'done');
  const errorAgents = subAgentStates.filter(a => a.state === 'error');
  const orchestratorWorking = orchestratorState?.state === 'working' ? orchestratorState : null;
  const hasStatus = workingAgents.length > 0 || doneAgents.length > 0 || errorAgents.length > 0 || orchestratorWorking;

  if (!hasStatus) return null;

  return (
    <div className="flex-shrink-0 px-4 py-1.5 flex items-center gap-3 overflow-x-auto"
      style={{ borderBottom: '1px solid var(--border-dim)', background: 'linear-gradient(180deg, var(--bg-panel), var(--bg-void))' }}>
      {/* Orchestrator chip */}
      {orchestratorWorking && (() => {
        const ac = getAgentAccent('orchestrator');
        const elapsedSec = orchestratorWorking.started_at ? Math.round((now - orchestratorWorking.started_at) / 1000) : 0;
        return (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg flex-shrink-0 animate-[fadeSlideIn_0.2s_ease-out]"
            style={{ background: ac.bg, border: `1px solid ${ac.color}30` }}>
            <div className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse" style={{ background: ac.color }} />
            <span className="text-[11px] font-semibold" style={{ color: ac.color }}>
              🎯 Orchestrator
            </span>
            {elapsedSec > 0 && (
              <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
                {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
              </span>
            )}
            {orchestratorWorking.current_tool && (
              <span className="text-[10px] leading-tight" style={{ color: `${ac.color}99`, fontFamily: 'var(--font-mono)', maxWidth: '200px', display: '-webkit-box', WebkitLineClamp: 1, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                {orchestratorWorking.current_tool}
              </span>
            )}
          </div>
        );
      })()}
      {workingAgents.map(agent => {
        const ac = getAgentAccent(agent.name);
        const elapsedSec = agent.started_at ? Math.round((now - agent.started_at) / 1000) : 0;
        const isStale = agent.last_update_at ? (now - agent.last_update_at) > 90000 : (agent.started_at ? (now - agent.started_at) > 90000 : false);
        return (
          <div key={agent.name} className="flex items-center gap-2 px-2.5 py-1 rounded-lg flex-shrink-0 animate-[fadeSlideIn_0.2s_ease-out]"
            style={{ background: isStale ? 'rgba(245,166,35,0.06)' : ac.bg, border: `1px solid ${isStale ? 'rgba(245,166,35,0.25)' : ac.color + '25'}` }}>
            <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isStale ? '' : 'animate-pulse'}`} style={{ background: isStale ? 'var(--accent-amber)' : ac.color }} />
            <span className="text-[11px] font-semibold" style={{ color: isStale ? 'var(--accent-amber)' : ac.color }}>
              {AGENT_ICONS[agent.name] || '\u{1F527}'} {AGENT_LABELS[agent.name] || agent.name}
            </span>
            {elapsedSec > 0 && (
              <span className="text-[10px] font-mono" style={{ color: isStale ? 'var(--accent-amber)' : 'var(--text-muted)' }}>
                {elapsedSec >= 60 ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s` : `${elapsedSec}s`}
              </span>
            )}
            {isStale && (
              <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
                THINKING
              </span>
            )}
            {agent.current_tool && !isStale && (
              <span className="text-[10px] break-all leading-tight" style={{ color: `${ac.color}99`, fontFamily: 'var(--font-mono)', maxWidth: '300px', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                {agent.current_tool}
              </span>
            )}
          </div>
        );
      })}
      {doneAgents.length > 0 && (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg flex-shrink-0"
          style={{ background: 'rgba(61,214,140,0.04)', border: '1px solid rgba(61,214,140,0.12)' }}>
          <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent-green)' }} />
          <span className="text-[10px] font-medium" style={{ color: 'var(--accent-green)' }}>
            {doneAgents.length} done
          </span>
        </div>
      )}
      {errorAgents.length > 0 && (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg flex-shrink-0"
          style={{ background: 'rgba(245,71,91,0.04)', border: '1px solid rgba(245,71,91,0.12)' }}>
          <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent-red)' }} />
          <span className="text-[10px] font-medium" style={{ color: 'var(--accent-red)' }}>
            {errorAgents.length} error
          </span>
        </div>
      )}
      {lastTicker && (
        <span className="text-[10px] truncate ml-auto flex-shrink-0"
          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', maxWidth: '250px' }}>
          {lastTicker}
        </span>
      )}
    </div>
  );
});

// ============================================================================
// DesktopTabBar — Tab buttons for switching desktop views
// ============================================================================

export const DesktopTabBar = React.memo(function DesktopTabBar({
  desktopTab,
  onSetDesktopTab,
  projectStatus,
  activitiesCount,
  onShowClearConfirm,
}: DesktopTabBarProps): React.ReactElement {
  return (
    <div className="flex-shrink-0 px-4 py-2" style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-panel)' }}>
      <div className="flex items-center gap-1">
        {DESKTOP_TAB_ITEMS.map(tab => (
          <button
            key={tab.id}
            onClick={() => onSetDesktopTab(tab.id)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
            style={{
              background: desktopTab === tab.id ? 'var(--bg-elevated)' : 'transparent',
              color: desktopTab === tab.id ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
            aria-current={desktopTab === tab.id ? 'page' : undefined}
            aria-label={`${tab.label} tab`}
          >
            {tab.icon}
            <span>{tab.label}</span>
          </button>
        ))}
        {/* Clear history — desktop */}
        {projectStatus === 'idle' && activitiesCount > 0 && (
          <button onClick={onShowClearConfirm} className="ml-auto p-1.5 rounded-lg transition-all hover:bg-[var(--bg-elevated)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-red)]"
            style={{ color: 'var(--text-muted)' }} title="Clear history" aria-label="Clear history">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
});

// ============================================================================
// HivemindTabContent — Agent cards + metrics + self-healing (merged view)
// ============================================================================

export const HivemindTabContent = React.memo(function HivemindTabContent({
  agentStateList,
  projectStatus,
  healingEvents,
  selectedAgent,
  onSelectAgent,
  agentMetrics,
}: HivemindTabContentProps): React.ReactElement {
  const workingAgents = agentStateList.filter(a => a.state === 'working' && a.name !== 'orchestrator');
  const doneAgents = agentStateList.filter(a => a.state === 'done' && a.name !== 'orchestrator');
  const errorAgents = agentStateList.filter(a => a.state === 'error' && a.name !== 'orchestrator');
  const isRunning = projectStatus === 'running';

  return (
    <div className="p-6 space-y-5">
      {/* Status summary strip — shows when agents are running */}
      {isRunning && workingAgents.length > 0 && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl animate-[fadeSlideIn_0.3s_ease-out]"
          style={{
            background: 'linear-gradient(135deg, rgba(99,140,255,0.06), rgba(139,92,246,0.04))',
            border: '1px solid rgba(99,140,255,0.12)',
          }}>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: 'var(--accent-blue)' }} />
            <span className="text-xs font-bold tracking-wider" style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
              {workingAgents.length} ACTIVE
            </span>
          </div>
          {doneAgents.length > 0 && (
            <span className="text-[10px] font-medium" style={{ color: 'var(--accent-green)' }}>
              {doneAgents.length} done
            </span>
          )}
          {errorAgents.length > 0 && (
            <span className="text-[10px] font-medium" style={{ color: 'var(--accent-red)' }}>
              {errorAgents.length} failed
            </span>
          )}
          <div className="flex-1" />
          <div className="flex -space-x-2">
            {workingAgents.slice(0, 5).map(a => {
              const ac = getAgentAccent(a.name);
              return (
                <div key={a.name} className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] ring-2 ring-[var(--bg-panel)]"
                  style={{ background: ac.bg, border: `1px solid ${ac.color}40` }}>
                  {AGENT_ICONS[a.name] || '\u{1F527}'}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Agent cards grid */}
      <AgentStatusPanel
        agents={agentStateList}
        onSelectAgent={onSelectAgent}
        selectedAgent={selectedAgent}
        layout="grid"
      />

      {/* Agent metrics (when available) */}
      {agentMetrics.length > 0 && (
        <AgentMetrics metrics={agentMetrics} />
      )}

      {/* Self-Healing Events */}
      {healingEvents.length > 0 && (
        <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid rgba(245,158,11,0.2)' }}>
          <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
            Self-Healing ({healingEvents.length})
          </h3>
          <div className="space-y-2">
            {healingEvents.map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span className="px-1.5 py-0.5 rounded" style={{ background: 'var(--glow-red)', color: 'var(--accent-red)', fontSize: '10px' }}>{h.failure_category}</span>
                <span>{h.failed_task}</span>
                <span style={{ color: 'var(--text-muted)' }}>→</span>
                <span className="font-mono" style={{ color: 'var(--accent-green)' }}>{h.remediation_role}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

export default HivemindTabContent;
