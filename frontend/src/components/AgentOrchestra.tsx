import React, { useMemo, useState, useEffect, useRef } from 'react';
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
  const workingAgents = subAgentStates.filter(a => a.state === 'working' || a.state === 'waiting');
  const doneAgents = subAgentStates.filter(a => a.state === 'done');
  const errorAgents = subAgentStates.filter(a => a.state === 'error');
  const orchestratorWorking = (orchestratorState?.state === 'working' || orchestratorState?.state === 'waiting') ? orchestratorState : null;
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
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
            style={{
              background: desktopTab === tab.id
                ? 'linear-gradient(135deg, rgba(99,140,255,0.12), rgba(139,92,246,0.08))'
                : 'transparent',
              color: desktopTab === tab.id ? 'var(--text-primary)' : 'var(--text-muted)',
              border: desktopTab === tab.id ? '1px solid rgba(99,140,255,0.15)' : '1px solid transparent',
              boxShadow: desktopTab === tab.id ? '0 1px 4px rgba(99,140,255,0.1)' : 'none',
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
// AgentOrchestraViz — Circular SVG network with animated data-stream lines
// ============================================================================

interface OrchestraVizProps {
  agents: AgentStateType[];
  onSelectAgent: (agent: string | null) => void;
  selectedAgent: string | null;
}

interface NodePosition {
  x: number;
  y: number;
  name: string;
  state: string;
}

interface ConnectorLine {
  from: NodePosition;
  to: NodePosition;
  key: string;
}

const VIZ_SIZE = 280;
const VIZ_CENTER = VIZ_SIZE / 2;
const ORBIT_RADIUS = 90;
const NODE_RADIUS = 22;
const ORBITAL_RING_RADIUS = NODE_RADIUS + 8;
const MAX_ACTIVE_ANIMATIONS = 12;

const AgentOrchestraViz = React.memo(function AgentOrchestraViz({
  agents,
  onSelectAgent,
  selectedAgent,
}: OrchestraVizProps): React.ReactElement | null {
  const subAgents = agents.filter(a => a.name !== 'orchestrator');

  const nodePositions = useMemo((): NodePosition[] => {
    if (subAgents.length === 0) return [];
    const count = subAgents.length;
    return subAgents.map((agent, i) => {
      const angle = (2 * Math.PI * i) / count - Math.PI / 2;
      return {
        x: VIZ_CENTER + ORBIT_RADIUS * Math.cos(angle),
        y: VIZ_CENTER + ORBIT_RADIUS * Math.sin(angle),
        name: agent.name,
        state: agent.state,
      };
    });
  }, [subAgents.length, ...subAgents.map(a => `${a.name}:${a.state}`)]);

  const connectorLines = useMemo((): ConnectorLine[] => {
    const lines: ConnectorLine[] = [];
    const posMap = new Map(nodePositions.map(n => [n.name, n]));

    for (const agent of subAgents) {
      if (agent.delegated_from && (agent.state === 'working' || agent.state === 'waiting' || agent.state === 'done')) {
        const fromPos = posMap.get(agent.delegated_from);
        const toPos = posMap.get(agent.name);
        if (fromPos && toPos) {
          lines.push({
            from: fromPos,
            to: toPos,
            key: `${agent.delegated_from}->${agent.name}`,
          });
        }
      }
    }

    // Also connect done→working pairs by sequence (adjacency in the list)
    const doneNames = new Set(subAgents.filter(a => a.state === 'done').map(a => a.name));
    const workingNames = subAgents.filter(a => a.state === 'working' || a.state === 'waiting');
    for (const working of workingNames) {
      if (working.delegated_from) continue; // already handled
      // Connect from nearest done agent
      for (const doneName of doneNames) {
        const fromPos = posMap.get(doneName);
        const toPos = posMap.get(working.name);
        if (fromPos && toPos) {
          const lineKey = `${doneName}->${working.name}`;
          if (!lines.some(l => l.key === lineKey)) {
            lines.push({ from: fromPos, to: toPos, key: lineKey });
          }
        }
      }
    }

    return lines.slice(0, MAX_ACTIVE_ANIMATIONS);
  }, [nodePositions, subAgents]);

  // ---- Live status text with 500ms debounce ----
  const MAX_CENTER_CHARS = 32;

  const rawStatusText = useMemo((): string => {
    const working = subAgents.filter(a => a.state === 'working' || a.state === 'waiting');
    const done = subAgents.filter(a => a.state === 'done');
    if (working.length > 0) {
      const primary = working[0];
      const label = AGENT_LABELS[primary.name] || primary.name;
      if (primary.task) return `${label}: ${primary.task}`;
      if (primary.current_tool) return `${label} → ${primary.current_tool}`;
      return working.length > 1
        ? `${working.length} agents working...`
        : `${label} is working...`;
    }
    if (done.length > 0) return done.length === subAgents.length ? 'All agents complete' : `${done.length} done`;
    return 'READY';
  }, [subAgents]);

  // Truncate for SVG display
  const truncateText = (text: string, max: number): string =>
    text.length > max ? text.slice(0, max - 1) + '…' : text;

  // Debounced display text — waits 500ms before committing a new value
  const [displayText, setDisplayText] = useState<string>(rawStatusText);
  const [textKey, setTextKey] = useState<number>(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevRawRef = useRef<string>(rawStatusText);

  useEffect(() => {
    if (rawStatusText === prevRawRef.current) return;
    prevRawRef.current = rawStatusText;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDisplayText(rawStatusText);
      setTextKey(k => k + 1); // re-trigger typewriter
    }, 500);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [rawStatusText]);

  const centerLabel = truncateText(displayText, MAX_CENTER_CHARS);
  const isActiveStatus = displayText !== 'READY' && displayText !== 'idle';

  if (subAgents.length === 0) return null;

  return (
    <div className="flex justify-center animate-[fadeSlideIn_0.4s_ease-out]">
      <svg
        width={VIZ_SIZE}
        height={VIZ_SIZE}
        viewBox={`0 0 ${VIZ_SIZE} ${VIZ_SIZE}`}
        className="orchestra-viz"
        aria-label="Agent orchestra visualization"
        role="img"
      >
        <defs>
          <filter id="glow-active" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Connector lines between agents — pointer-events none so clicks pass through */}
        <g style={{ pointerEvents: 'none' }}>
          {connectorLines.map((line, idx) => {
            const dx = line.to.x - line.from.x;
            const dy = line.to.y - line.from.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 0.01) return null; // guard against division by zero
            const ux = dx / dist;
            const uy = dy / dist;
            // Shorten line to not overlap with node circles
            const x1 = line.from.x + ux * (NODE_RADIUS + 2);
            const y1 = line.from.y + uy * (NODE_RADIUS + 2);
            const x2 = line.to.x - ux * (NODE_RADIUS + 2);
            const y2 = line.to.y - uy * (NODE_RADIUS + 2);

            return (
              <g key={line.key}>
                {/* Glow under-line */}
                <line
                  x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke="var(--accent-blue)"
                  strokeWidth="3"
                  strokeOpacity="0.1"
                  strokeLinecap="round"
                />
                {/* Animated dash line */}
                <line
                  x1={x1} y1={y1} x2={x2} y2={y2}
                  className="orchestra-connector-line"
                  stroke="var(--accent-blue)"
                  strokeWidth="1.5"
                  strokeOpacity="0.6"
                  strokeDasharray="6 4"
                  strokeLinecap="round"
                  style={{
                    animation: `dashFlow ${1.5 + idx * 0.2}s linear infinite`,
                  }}
                />
                {/* Flowing dot along the line */}
                <circle r="2.5" fill="var(--accent-blue)" opacity="0.7" filter="url(#glow-active)">
                  <animateMotion
                    dur={`${2 + idx * 0.3}s`}
                    repeatCount="indefinite"
                    path={`M${x1},${y1} L${x2},${y2}`}
                  />
                </circle>
              </g>
            );
          })}
        </g>

        {/* Center status — dynamic live text with typewriter animation */}
        <foreignObject
          x={VIZ_CENTER - 60}
          y={VIZ_CENTER - 18}
          width={120}
          height={36}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              width: '100%',
              height: '100%',
            }}
          >
            <div
              key={textKey}
              className="orchestra-live-status"
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '10px',
                fontWeight: 600,
                letterSpacing: '0.05em',
                textTransform: 'uppercase',
                color: isActiveStatus ? 'var(--accent-blue)' : 'var(--text-muted)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                maxWidth: '120px',
                textAlign: 'center',
                borderRight: isActiveStatus ? '2px solid var(--accent-blue)' : 'none',
                animation: isActiveStatus
                  ? 'typewriter 0.8s steps(20, end) forwards, blink 0.7s step-end 0.8s infinite'
                  : 'none',
                width: isActiveStatus ? '0' : 'auto',
              }}
              aria-live="polite"
              aria-label={`Orchestra status: ${displayText}`}
            >
              {centerLabel}
            </div>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '8px',
                color: 'var(--text-muted)',
                opacity: 0.6,
                marginTop: '2px',
              }}
            >
              ORCHESTRA
            </span>
          </div>
        </foreignObject>

        {/* Agent nodes */}
        {nodePositions.map((node) => {
          const agent = subAgents.find(a => a.name === node.name);
          if (!agent) return null;
          const accent = getAgentAccent(agent.name);
          const isActive = agent.state === 'working' || agent.state === 'waiting';
          const isDone = agent.state === 'done';
          const isError = agent.state === 'error';
          const isSelected = selectedAgent === agent.name;
          const icon = AGENT_ICONS[agent.name] || '\u{1F527}';

          return (
            <g
              key={node.name}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelectAgent(agent.name)}
              role="button"
              tabIndex={0}
              aria-label={`${AGENT_LABELS[agent.name] || agent.name}: ${agent.state}`}
            >
              {/* Orbital glow ring — only for active agents, uses --glow-blue */}
              {isActive && (
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={ORBITAL_RING_RADIUS}
                  fill="none"
                  className="orchestra-orbital-ring"
                  stroke={accent.color}
                  strokeWidth="1.5"
                  strokeDasharray="8 6"
                  strokeOpacity="0.5"
                  style={{
                    transformOrigin: `${node.x}px ${node.y}px`,
                    animation: 'orbitalSpin 4s linear infinite',
                    filter: 'drop-shadow(0 0 6px var(--glow-blue))',
                  }}
                />
              )}
              {/* Active glow aura — --glow-blue highlight */}
              {isActive && (
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={NODE_RADIUS + 3}
                  fill="none"
                  stroke={accent.color}
                  strokeWidth="2"
                  strokeOpacity="0.3"
                  filter="url(#glow-active)"
                  className="animate-pulse"
                  style={{ filter: 'drop-shadow(0 0 10px var(--glow-blue))' }}
                />
              )}
              {/* Node background circle */}
              <circle
                cx={node.x}
                cy={node.y}
                r={NODE_RADIUS}
                fill="var(--bg-elevated)"
                stroke={
                  isActive ? accent.color :
                  isDone ? 'var(--accent-green)' :
                  isError ? 'var(--accent-red)' :
                  'var(--border-subtle)'
                }
                strokeWidth={isActive || isSelected ? 2 : 1}
                strokeOpacity={isActive ? 0.8 : isDone ? 0.5 : isError ? 0.5 : 0.3}
                style={{
                  filter: isActive
                    ? 'drop-shadow(0 0 10px var(--glow-blue))'
                    : isDone
                    ? 'drop-shadow(0 0 6px var(--glow-green))'
                    : 'none',
                  transition: 'all 0.3s ease',
                }}
              />
              {/* Done glow ring — uses --glow-green */}
              {isDone && (
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={NODE_RADIUS + 3}
                  fill="none"
                  stroke="var(--accent-green)"
                  strokeWidth="1"
                  strokeOpacity="0.25"
                  style={{ filter: 'drop-shadow(0 0 6px var(--glow-green))' }}
                />
              )}
              {/* Agent icon */}
              <text
                x={node.x}
                y={node.y + 1}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize="16"
                style={{
                  opacity: agent.state === 'idle' ? 0.4 : 1,
                  transition: 'opacity 0.3s ease',
                }}
              >
                {icon}
              </text>
              {/* Agent label */}
              <text
                x={node.x}
                y={node.y + NODE_RADIUS + 14}
                textAnchor="middle"
                fontSize="9"
                fontWeight="600"
                fill={
                  isActive ? accent.color :
                  isDone ? 'var(--accent-green)' :
                  isError ? 'var(--accent-red)' :
                  'var(--text-muted)'
                }
                style={{ transition: 'fill 0.3s ease' }}
              >
                {AGENT_LABELS[agent.name] || agent.name}
              </text>
              {/* Status indicator dot */}
              <circle
                cx={node.x + NODE_RADIUS - 4}
                cy={node.y - NODE_RADIUS + 4}
                r="3.5"
                fill={
                  isActive ? accent.color :
                  isDone ? 'var(--accent-green)' :
                  isError ? 'var(--accent-red)' :
                  'var(--text-muted)'
                }
                stroke="var(--bg-elevated)"
                strokeWidth="1.5"
                className={isActive ? 'animate-pulse' : ''}
              />
            </g>
          );
        })}
      </svg>
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
  const workingAgents = agentStateList.filter(a => (a.state === 'working' || a.state === 'waiting') && a.name !== 'orchestrator');
  const doneAgents = agentStateList.filter(a => a.state === 'done' && a.name !== 'orchestrator');
  const errorAgents = agentStateList.filter(a => a.state === 'error' && a.name !== 'orchestrator');
  const isRunning = projectStatus === 'running';
  const hasActiveAgents = agentStateList.some(a => a.state !== 'idle' && a.name !== 'orchestrator');

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

      {/* Agent Orchestra Visualization — circular SVG network */}
      {hasActiveAgents && agentStateList.filter(a => a.name !== 'orchestrator').length > 1 && (
        <AgentOrchestraViz
          agents={agentStateList}
          onSelectAgent={onSelectAgent}
          selectedAgent={selectedAgent}
        />
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
