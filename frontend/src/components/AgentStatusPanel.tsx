import type { AgentState } from '../types';
import { useState } from 'react';
import { AGENT_ICONS, AGENT_LABELS } from '../constants';

interface Props {
  agents: AgentState[];
  onSelectAgent?: (name: string) => void;
  selectedAgent?: string | null;
  layout?: 'grid' | 'compact' | 'bubbles';
}

// Agent-specific accent colors (HSL-based for glow calculations)
const AGENT_ACCENTS: Record<string, { color: string; glow: string; bg: string }> = {
  developer: { color: '#638cff', glow: 'rgba(99,140,255,0.2)', bg: 'rgba(99,140,255,0.06)' },
  reviewer:  { color: '#a78bfa', glow: 'rgba(167,139,250,0.2)', bg: 'rgba(167,139,250,0.06)' },
  tester:    { color: '#f5a623', glow: 'rgba(245,166,35,0.2)', bg: 'rgba(245,166,35,0.06)' },
  devops:    { color: '#22d3ee', glow: 'rgba(34,211,238,0.2)', bg: 'rgba(34,211,238,0.06)' },
  orchestrator: { color: '#8b90a5', glow: 'rgba(139,144,165,0.15)', bg: 'rgba(139,144,165,0.05)' },
};

function getAccent(name: string) {
  return AGENT_ACCENTS[name] || AGENT_ACCENTS.orchestrator;
}

function stateStyles(state: string, agentName: string) {
  const accent = getAccent(agentName);
  switch (state) {
    case 'working': return {
      border: `1px solid ${accent.color}40`,
      boxShadow: `0 0 20px -4px ${accent.glow}, inset 0 1px 0 0 ${accent.color}08`,
      dotColor: accent.color,
      pulse: true,
      label: 'ACTIVE',
      labelColor: accent.color,
      bgTint: accent.bg,
    };
    case 'done': return {
      border: '1px solid rgba(61,214,140,0.2)',
      boxShadow: '0 0 12px -4px rgba(61,214,140,0.12)',
      dotColor: '#3dd68c',
      pulse: false,
      label: 'DONE',
      labelColor: '#3dd68c',
      bgTint: 'rgba(61,214,140,0.04)',
    };
    case 'error': return {
      border: '1px solid rgba(245,71,91,0.25)',
      boxShadow: '0 0 12px -4px rgba(245,71,91,0.15)',
      dotColor: '#f5475b',
      pulse: false,
      label: 'ERROR',
      labelColor: '#f5475b',
      bgTint: 'rgba(245,71,91,0.04)',
    };
    default: return {
      border: '1px solid rgba(255,255,255,0.04)',
      boxShadow: 'none',
      dotColor: '#4a4e63',
      pulse: false,
      label: 'STANDBY',
      labelColor: '#4a4e63',
      bgTint: 'transparent',
    };
  }
}

function isRecentDelegation(agent: AgentState): boolean {
  if (!agent.delegated_at) return false;
  return Date.now() - agent.delegated_at < 5000;
}

export default function AgentStatusPanel({ agents, onSelectAgent, selectedAgent, layout = 'grid' }: Props) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  if (agents.length === 0) {
    return (
      <div className="text-[var(--text-muted)] text-sm italic p-8 text-center font-[var(--font-display)]">
        No agents registered
      </div>
    );
  }

  const subAgents = agents.filter(a => a.name !== 'orchestrator');

  // === COMPACT LAYOUT ===
  if (layout === 'compact') {
    return (
      <div className="space-y-1.5 px-1">
        {subAgents.map((agent) => {
          const s = stateStyles(agent.state, agent.name);
          const icon = AGENT_ICONS[agent.name] || '🔧';
          return (
            <div
              key={agent.name}
              className="rounded-lg px-3 py-2 transition-all duration-300"
              style={{
                background: `var(--bg-card)`,
                border: s.border,
                boxShadow: s.boxShadow,
              }}
            >
              <div className="flex items-center gap-2.5">
                <div className="relative flex-shrink-0">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center text-sm"
                    style={{ background: s.bgTint }}>
                    {icon}
                  </div>
                  <div
                    className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-[var(--bg-card)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor }}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[var(--text-primary)] capitalize">{agent.name}</span>
                    <span className="text-[9px] font-bold tracking-[0.08em]" style={{ color: s.labelColor, fontFamily: 'var(--font-mono)' }}>
                      {s.label}
                    </span>
                  </div>
                  {agent.state === 'working' && agent.current_tool && (
                    <p className="text-[10px] font-[var(--font-mono)] truncate mt-0.5 text-fade-right"
                      style={{ color: `${getAccent(agent.name).color}99` }}>
                      {agent.current_tool}
                    </p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // === BUBBLES LAYOUT ===
  if (layout === 'bubbles') {
    const expanded = expandedAgent ? subAgents.find(a => a.name === expandedAgent) : null;
    return (
      <div>
        <div className="flex flex-wrap justify-center gap-5 py-3">
          {subAgents.map((agent) => {
            const s = stateStyles(agent.state, agent.name);
            const icon = AGENT_ICONS[agent.name] || '🔧';
            const label = AGENT_LABELS[agent.name] || agent.name;
            const isSelected = expandedAgent === agent.name;
            const accent = getAccent(agent.name);

            return (
              <button
                key={agent.name}
                onClick={() => setExpandedAgent(isSelected ? null : agent.name)}
                className="flex flex-col items-center gap-2 group"
              >
                <div className="relative transition-all duration-500">
                  {/* Orbital ring for working agents */}
                  {agent.state === 'working' && (
                    <div
                      className="absolute inset-[-4px] rounded-full animate-[orbitalSpin_3s_linear_infinite]"
                      style={{
                        border: `1.5px dashed ${accent.color}30`,
                      }}
                    />
                  )}
                  <div
                    className={`w-14 h-14 rounded-2xl flex items-center justify-center text-2xl transition-all duration-500 ${isSelected ? 'scale-110' : ''}`}
                    style={{
                      background: agent.state === 'idle' ? 'var(--bg-elevated)' : s.bgTint,
                      border: s.border,
                      boxShadow: s.boxShadow,
                      opacity: agent.state === 'idle' ? 0.5 : 1,
                    }}
                  >
                    {icon}
                  </div>
                  <div
                    className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[var(--bg-void)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor }}
                  />
                </div>
                <span className="text-[10px] font-semibold transition-colors" style={{ color: s.labelColor }}>
                  {label}
                </span>
              </button>
            );
          })}
        </div>

        {/* Expanded detail */}
        {expanded && (
          <div
            className="mt-3 rounded-xl p-4 animate-[slideUp_0.25s_ease-out]"
            style={{
              background: 'var(--bg-card)',
              border: stateStyles(expanded.state, expanded.name).border,
            }}
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center text-lg"
                style={{ background: stateStyles(expanded.state, expanded.name).bgTint }}>
                {AGENT_ICONS[expanded.name] || '🔧'}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-bold text-[var(--text-primary)]">
                  {AGENT_LABELS[expanded.name] || expanded.name}
                </h3>
                <span className="text-[10px] font-bold tracking-[0.08em]"
                  style={{ color: stateStyles(expanded.state, expanded.name).labelColor, fontFamily: 'var(--font-mono)' }}>
                  {stateStyles(expanded.state, expanded.name).label}
                </span>
              </div>
              <button onClick={() => setExpandedAgent(null)}
                className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] p-1 transition-colors">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
              </button>
            </div>
            {expanded.task && <p className="text-xs text-[var(--text-secondary)] mb-3 leading-relaxed">{expanded.task}</p>}
            {expanded.state === 'working' && expanded.current_tool && (
              <ToolActivity tool={expanded.current_tool} agentName={expanded.name} />
            )}
            <AgentStats agent={expanded} />
          </div>
        )}
      </div>
    );
  }

  // === GRID LAYOUT (default) ===
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {subAgents.map((agent, index) => {
        const s = stateStyles(agent.state, agent.name);
        const icon = AGENT_ICONS[agent.name] || '🔧';
        const label = AGENT_LABELS[agent.name] || agent.name;
        const isExpanded = expandedAgent === agent.name;
        const isSelected = selectedAgent === agent.name;
        const recentDelegation = isRecentDelegation(agent);
        const accent = getAccent(agent.name);

        return (
          <div
            key={agent.name}
            className={`relative rounded-xl transition-all duration-300 cursor-pointer card-hover overflow-hidden
              ${recentDelegation ? 'animate-[delegationPulse_1.5s_ease-out]' : ''}
              ${isSelected ? 'ring-1 ring-[var(--accent-blue)]/30' : ''}
              ${agent.state === 'working' ? 'agent-card-working' : ''}`}
            style={{
              background: 'var(--bg-card)',
              border: s.border,
              boxShadow: s.boxShadow,
              borderLeft: `3px solid ${accent.color}${agent.state === 'idle' ? '15' : '60'}`,
              animationDelay: `${index * 50}ms`,
              animation: 'slideUp 0.3s ease-out backwards',
            }}
            onClick={() => {
              if (onSelectAgent) onSelectAgent(agent.name);
              setExpandedAgent(isExpanded ? null : agent.name);
            }}
          >
            {/* Delegation banner */}
            {recentDelegation && agent.delegated_from && (
              <div className="px-4 pt-3 pb-0 animate-[fadeSlideIn_0.3s_ease-out] relative z-10">
                <div className="flex items-center gap-1.5 text-[10px] rounded-md px-2.5 py-1.5"
                  style={{ background: `${accent.color}10`, color: accent.color, fontFamily: 'var(--font-mono)' }}>
                  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M1 8h10M8 4l4 4-4 4"/>
                  </svg>
                  Task from <span className="font-semibold capitalize">{agent.delegated_from}</span>
                </div>
              </div>
            )}

            {/* Card content */}
            <div className="p-4 relative z-10">
              {/* Header */}
              <div className="flex items-center gap-3 mb-3">
                <div className="relative flex-shrink-0">
                  <div className="w-11 h-11 rounded-xl flex items-center justify-center text-lg transition-all duration-500"
                    style={{ background: s.bgTint }}>
                    {icon}
                  </div>
                  <div
                    className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[var(--bg-card)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor }}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-[13px] font-bold text-[var(--text-primary)]">{label}</h3>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9px] font-bold tracking-[0.1em]"
                      style={{ color: s.labelColor, fontFamily: 'var(--font-mono)' }}>
                      {s.label}
                    </span>
                    {agent.state === 'working' && agent.duration > 0 && (
                      <span className="text-[9px] text-[var(--text-muted)]" style={{ fontFamily: 'var(--font-mono)' }}>
                        {Math.round(agent.duration)}s
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Task description */}
              {agent.task && (
                <p className={`text-xs mb-2.5 leading-relaxed ${agent.state === 'working' ? 'text-[var(--text-secondary)]' : 'text-[var(--text-muted)]'}`}>
                  {agent.task.length > 120 ? agent.task.slice(0, 120) + '…' : agent.task}
                </p>
              )}

              {/* Current tool activity */}
              {agent.state === 'working' && agent.current_tool && (
                <ToolActivity tool={agent.current_tool} agentName={agent.name} />
              )}

              {/* Last result */}
              {(agent.state === 'done' || agent.state === 'error') && agent.last_result && (
                <div className="text-[11px] rounded-lg px-3 py-2 mb-2.5 truncate"
                  style={{
                    background: agent.state === 'done' ? 'rgba(61,214,140,0.04)' : 'rgba(245,71,91,0.04)',
                    color: agent.state === 'done' ? '#3dd68c99' : '#f5475b99',
                  }}>
                  {agent.last_result.replace(/\*\w+\*\s*/, '').slice(0, 120)}
                </div>
              )}

              {/* Idle placeholder */}
              {agent.state === 'idle' && !agent.task && (
                <p className="text-xs text-[var(--text-muted)] italic">Ready for tasks</p>
              )}

              {/* Progress bar for working agents */}
              {agent.state === 'working' && (
                <div className="h-[2px] rounded-full overflow-hidden mt-3"
                  style={{ background: 'var(--border-dim)' }}>
                  <div className="h-full rounded-full animate-[loading_2s_ease-in-out_infinite]"
                    style={{ width: '60%', background: `linear-gradient(90deg, ${accent.color}, ${accent.color}80)` }} />
                </div>
              )}

              {/* Stats */}
              <AgentStats agent={agent} />
            </div>

            {/* Expanded */}
            {isExpanded && agent.task && (
              <div className="px-4 pb-4 relative z-10" style={{ borderTop: '1px solid var(--border-dim)' }}>
                <p className="text-xs text-[var(--text-secondary)] mt-3 whitespace-pre-wrap">{agent.task}</p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Animated tool activity indicator */
function ToolActivity({ tool, agentName }: { tool: string; agentName: string }) {
  const accent = getAccent(agentName);
  return (
    <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 mb-2.5"
      style={{ background: `${accent.color}08` }}>
      <div className="flex gap-[3px] flex-shrink-0">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="w-[5px] h-[5px] rounded-full animate-bounce"
            style={{
              backgroundColor: `${accent.color}90`,
              animationDelay: `${i * 150}ms`,
              animationDuration: '0.8s',
            }}
          />
        ))}
      </div>
      <span className="text-[11px] truncate text-fade-right"
        style={{ color: `${accent.color}cc`, fontFamily: 'var(--font-mono)' }}>
        {tool}
      </span>
    </div>
  );
}

/** Telemetry-style stats row */
function AgentStats({ agent }: { agent: AgentState }) {
  if (agent.cost <= 0 && agent.turns <= 0 && agent.duration <= 0) return null;
  return (
    <div className="flex items-center gap-3 mt-3 pt-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
      {agent.cost > 0 && (
        <span className="telemetry">${agent.cost.toFixed(3)}</span>
      )}
      {agent.turns > 0 && (
        <span className="telemetry" style={{ color: 'var(--text-muted)' }}>
          {agent.turns} turns
        </span>
      )}
      {agent.duration > 0 && (
        <span className="telemetry" style={{ color: 'var(--text-muted)' }}>
          {Math.round(agent.duration)}s
        </span>
      )}
    </div>
  );
}
