import type { AgentState } from '../types';
import { useState, useEffect } from 'react';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

interface Props {
  agents: AgentState[];
  onSelectAgent?: (name: string) => void;
  selectedAgent?: string | null;
  layout?: 'grid' | 'compact' | 'bubbles';
}

function stateStyles(state: string, agentName: string) {
  const accent = getAgentAccent(agentName);
  switch (state) {
    case 'working': return {
      border: `1px solid ${accent.color}40`,
      boxShadow: `0 0 20px -4px ${accent.glow}, inset 0 1px 0 0 ${accent.color}08`,
      dotColor: accent.color, pulse: true, label: 'ACTIVE', labelColor: accent.color, bgTint: accent.bg,
    };
    case 'waiting': return {
      border: `1px solid ${accent.color}30`,
      boxShadow: `0 0 12px -4px ${accent.glow}`,
      dotColor: accent.color, pulse: true, label: 'WAITING', labelColor: accent.color, bgTint: accent.bg,
    };
    case 'done': return {
      border: '1px solid rgba(61,214,140,0.2)', boxShadow: '0 0 12px -4px rgba(61,214,140,0.12)',
      dotColor: '#3dd68c', pulse: false, label: 'DONE', labelColor: '#3dd68c', bgTint: 'rgba(61,214,140,0.04)',
    };
    case 'error': return {
      border: '1px solid rgba(245,71,91,0.25)', boxShadow: '0 0 12px -4px rgba(245,71,91,0.15)',
      dotColor: '#f5475b', pulse: false, label: 'ERROR', labelColor: '#f5475b', bgTint: 'rgba(245,71,91,0.04)',
    };
    default: return {
      border: '1px solid rgba(255,255,255,0.04)', boxShadow: 'none',
      dotColor: '#4a4e63', pulse: false, label: 'STANDBY', labelColor: '#4a4e63', bgTint: 'transparent',
    };
  }
}

function isRecentDelegation(agent: AgentState): boolean {
  if (!agent.delegated_at) return false;
  return Date.now() - agent.delegated_at < 5000;
}

export default function AgentStatusPanel({ agents, onSelectAgent, selectedAgent, layout = 'grid' }: Props) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [soloAgent, setSoloAgent] = useState<string | null>(null);

  // Tick for live elapsed time (every 5s)
  const [, setTick] = useState(0);
  const anyWorking = agents.some(a => a.state === 'working' || a.state === 'waiting');
  useEffect(() => {
    if (!anyWorking) return;
    const timer = setInterval(() => setTick(t => t + 1), 5000);
    return () => clearInterval(timer);
  }, [anyWorking]);

  if (agents.length === 0) {
    return (
      <div className="text-sm italic p-8 text-center" style={{ color: 'var(--text-muted)' }}>
        No agents registered
      </div>
    );
  }

  const subAgents = agents.filter(a => a.name !== 'orchestrator');
  const workingAgents = subAgents.filter(a => a.state === 'working' || a.state === 'waiting');

  // === COMPACT LAYOUT ===
  if (layout === 'compact') {
    return (
      <div className="space-y-1.5 px-1">
        {subAgents.map((agent) => {
          const s = stateStyles(agent.state, agent.name);
          const icon = AGENT_ICONS[agent.name] || '🔧';
          return (
            <div key={agent.name} className="rounded-lg px-3 py-2 transition-all duration-300"
              style={{ background: 'var(--bg-card)', border: s.border, boxShadow: s.boxShadow }}>
              <div className="flex items-center gap-2.5">
                <div className="relative flex-shrink-0">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center text-sm" style={{ background: s.bgTint }}>{icon}</div>
                  <div className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-[var(--bg-card)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold capitalize" style={{ color: 'var(--text-primary)' }}>{agent.name}</span>
                    <span className="text-[9px] font-bold tracking-[0.08em]" style={{ color: s.labelColor, fontFamily: 'var(--font-mono)' }}>{s.label}</span>
                  </div>
                  {(agent.state === 'working' || agent.state === 'waiting') && agent.current_tool && (
                    <p className="text-[10px] truncate mt-0.5 text-fade-right" style={{ color: `${getAgentAccent(agent.name).color}99`, fontFamily: 'var(--font-mono)' }}>
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

  // === BUBBLES LAYOUT (Mobile) ===
  if (layout === 'bubbles') {
    const visibleAgents = soloAgent ? subAgents.filter(a => a.name === soloAgent) : subAgents;
    const expanded = expandedAgent ? subAgents.find(a => a.name === expandedAgent) : null;

    return (
      <div className="flex flex-col h-full">
        {/* Solo mode banner */}
        {soloAgent && (
          <div className="flex items-center justify-between px-4 py-2 mb-2 rounded-xl animate-[fadeSlideIn_0.2s_ease-out]"
            style={{ background: `${getAgentAccent(soloAgent).color}10`, border: `1px solid ${getAgentAccent(soloAgent).color}20` }}>
            <span className="text-xs font-semibold" style={{ color: getAgentAccent(soloAgent).color }}>
              🔍 Solo: {AGENT_LABELS[soloAgent] || soloAgent}
            </span>
            <button onClick={() => setSoloAgent(null)} className="text-xs px-2 py-0.5 rounded-full transition-all active:scale-95"
              style={{ color: 'var(--text-muted)', background: 'var(--bg-elevated)' }}>
              Show All
            </button>
          </div>
        )}

        {/* Agent bubbles */}
        <div className={`flex flex-wrap justify-center gap-5 py-3 ${soloAgent ? 'gap-8' : ''}`}>
          {visibleAgents.map((agent) => {
            const s = stateStyles(agent.state, agent.name);
            const icon = AGENT_ICONS[agent.name] || '🔧';
            const label = AGENT_LABELS[agent.name] || agent.name;
            const isSelected = expandedAgent === agent.name;
            const accent = getAgentAccent(agent.name);
            const isSolo = soloAgent === agent.name;

            return (
              <button
                key={agent.name}
                onClick={() => setExpandedAgent(isSelected ? null : agent.name)}
                onDoubleClick={() => setSoloAgent(soloAgent === agent.name ? null : agent.name)}
                className="flex flex-col items-center gap-2 group"
                title="Tap to expand • Double-tap for solo mode"
              >
                <div className={`relative transition-all duration-500 ${isSolo ? 'scale-125' : ''}`}>
                  {/* Orbital ring for working/waiting agents */}
                  {(agent.state === 'working' || agent.state === 'waiting') && (
                    <div className="absolute inset-[-6px] rounded-full animate-[orbitalSpin_3s_linear_infinite]"
                      style={{ border: `1.5px dashed ${accent.color}40` }} />
                  )}
                  {/* Outer glow ring for working/waiting */}
                  {(agent.state === 'working' || agent.state === 'waiting') && (
                    <div className="absolute inset-[-2px] rounded-2xl animate-pulse"
                      style={{ boxShadow: `0 0 20px ${accent.glow}` }} />
                  )}
                  <div
                    className={`w-16 h-16 rounded-2xl flex items-center justify-center text-2xl transition-all duration-500`}
                    style={{
                      background: agent.state === 'idle' ? 'var(--bg-elevated)' : s.bgTint,
                      border: isSelected ? `2px solid ${accent.color}` : s.border,
                      boxShadow: isSelected ? `0 0 12px ${accent.glow}, ${s.boxShadow || 'none'}` : s.boxShadow,
                      opacity: agent.state === 'idle' && !soloAgent ? 0.5 : 1,
                      transform: isSelected ? 'scale(1.1)' : 'scale(1)',
                    }}
                  >
                    {icon}
                  </div>
                  <div
                    className={`absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-[var(--bg-void)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor, boxShadow: s.pulse ? `0 0 8px ${s.dotColor}` : 'none' }}
                  />
                </div>
                <span className="text-[11px] font-semibold transition-colors" style={{ color: s.labelColor }}>
                  {label}
                </span>
              </button>
            );
          })}
        </div>

        {/* === THE STAGE — fills the empty space === */}
        {!expanded && (
          <div className="flex-1 flex flex-col items-center justify-center px-4 mt-2">
            {anyWorking ? (
              /* Live activity stage */
              <div className="w-full max-w-sm animate-[fadeSlideIn_0.3s_ease-out]">
                <div className="text-center mb-4">
                  <span className="text-[10px] font-bold tracking-[0.15em] uppercase"
                    style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    ● LIVE ACTIVITY
                  </span>
                </div>

                {/* Activity cards for each working agent */}
                <div className="space-y-2.5">
                  {workingAgents.map(agent => {
                    const accent = getAgentAccent(agent.name);
                    return (
                      <div key={agent.name}
                        className="rounded-xl p-3.5 transition-all duration-300 animate-[slideUp_0.3s_ease-out]"
                        style={{
                          background: 'var(--bg-card)',
                          border: `1px solid ${accent.color}20`,
                          boxShadow: `0 0 15px ${accent.glow}`,
                        }}
                        onClick={() => { setExpandedAgent(agent.name); if (onSelectAgent) onSelectAgent(agent.name); }}
                      >
                        <div className="flex items-center gap-3">
                          {/* Pulse dot */}
                          <div className="relative flex-shrink-0">
                            <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: accent.color }} />
                            <div className="absolute inset-0 w-2 h-2 rounded-full animate-ping" style={{ background: accent.color, opacity: 0.3 }} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-bold" style={{ color: accent.color }}>
                                {AGENT_LABELS[agent.name] || agent.name}
                              </span>
                              {(() => {
                                const elapsed = agent.started_at ? Math.round((Date.now() - agent.started_at) / 1000) : (agent.duration > 0 ? Math.round(agent.duration) : 0);
                                const isStale = agent.last_update_at ? (Date.now() - agent.last_update_at) > 90000 : (agent.started_at ? (Date.now() - agent.started_at) > 90000 : false);
                                return (
                                  <>
                                    {elapsed > 0 && (
                                      <span className="text-[9px]" style={{ color: isStale ? 'var(--accent-amber)' : 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                                        {elapsed >= 60 ? `${Math.floor(elapsed / 60)}m${elapsed % 60}s` : `${elapsed}s`}
                                      </span>
                                    )}
                                    {isStale && (
                                      <span className="text-[8px] font-bold" style={{ color: 'var(--accent-amber)' }}>THINKING</span>
                                    )}
                                  </>
                                );
                              })()}
                            </div>
                            {agent.current_tool && (
                              <p className="text-[11px] truncate text-fade-right mt-0.5"
                                style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                                {agent.current_tool}
                              </p>
                            )}
                            {!agent.current_tool && agent.task && (
                              <p className="text-[11px] truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                                {agent.task.slice(0, 60)}
                              </p>
                            )}
                          </div>
                        </div>
                        {/* Mini progress bar */}
                        <div className="h-[2px] rounded-full overflow-hidden mt-3" style={{ background: 'var(--border-dim)' }}>
                          <div className="h-full rounded-full animate-[loading_2s_ease-in-out_infinite]"
                            style={{ width: '60%', background: `linear-gradient(90deg, ${accent.color}, ${accent.color}80)` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Delegation lines */}
                {workingAgents.filter(a => a.delegated_from).map(agent => (
                  <div key={`del-${agent.name}`}
                    className="flex items-center justify-center gap-2 mt-3 animate-[fadeSlideIn_0.3s_ease-out]">
                    <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {AGENT_LABELS[agent.delegated_from || ''] || agent.delegated_from}
                    </span>
                    <div className="flex items-center gap-0.5">
                      {[0,1,2].map(i => (
                        <span key={i} className="w-1 h-1 rounded-full animate-pulse"
                          style={{ background: 'var(--accent-blue)', animationDelay: `${i * 200}ms` }} />
                      ))}
                    </div>
                    <span className="text-[10px] font-semibold" style={{ color: getAgentAccent(agent.name).color }}>
                      {AGENT_LABELS[agent.name] || agent.name}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              /* Idle stage — animated network flow */
              <div className="text-center animate-[fadeSlideIn_0.5s_ease-out] w-full max-w-xs">
                {/* SVG network graph showing agent connections */}
                <svg viewBox="0 0 240 160" fill="none" className="w-full h-auto mb-4 opacity-60">
                  {/* Connection lines with flowing animation */}
                  <line x1="120" y1="30" x2="55" y2="80" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                    <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2s" repeatCount="indefinite"/>
                  </line>
                  <line x1="120" y1="30" x2="185" y2="80" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                    <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2.5s" repeatCount="indefinite"/>
                  </line>
                  <line x1="55" y1="80" x2="90" y2="135" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                    <animate attributeName="stroke-dashoffset" from="18" to="0" dur="3s" repeatCount="indefinite"/>
                  </line>
                  <line x1="185" y1="80" x2="150" y2="135" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                    <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2.2s" repeatCount="indefinite"/>
                  </line>

                  {/* Flowing dots along connections */}
                  <circle r="2" fill="var(--accent-blue)" opacity="0.5">
                    <animateMotion dur="3s" repeatCount="indefinite" path="M120,30 L55,80"/>
                  </circle>
                  <circle r="2" fill="var(--accent-purple)" opacity="0.5">
                    <animateMotion dur="3.5s" repeatCount="indefinite" path="M120,30 L185,80"/>
                  </circle>
                  <circle r="2" fill="var(--accent-cyan)" opacity="0.4">
                    <animateMotion dur="4s" repeatCount="indefinite" path="M55,80 L90,135"/>
                  </circle>

                  {/* Agent nodes */}
                  {/* Orchestrator (center top) */}
                  <circle cx="120" cy="30" r="14" fill="var(--bg-elevated)" stroke="var(--text-muted)" strokeWidth="1" opacity="0.7">
                    <animate attributeName="r" values="14;15;14" dur="3s" repeatCount="indefinite"/>
                  </circle>
                  <text x="120" y="34" textAnchor="middle" fontSize="12">🎯</text>

                  {/* Developer (left) */}
                  <circle cx="55" cy="80" r="12" fill="var(--bg-elevated)" stroke="rgba(99,140,255,0.3)" strokeWidth="1"/>
                  <text x="55" y="84" textAnchor="middle" fontSize="11">💻</text>

                  {/* Reviewer (right) */}
                  <circle cx="185" cy="80" r="12" fill="var(--bg-elevated)" stroke="rgba(167,139,250,0.3)" strokeWidth="1"/>
                  <text x="185" y="84" textAnchor="middle" fontSize="11">🔍</text>

                  {/* Tester (bottom-left) */}
                  <circle cx="90" cy="135" r="10" fill="var(--bg-elevated)" stroke="rgba(245,166,35,0.3)" strokeWidth="1"/>
                  <text x="90" y="139" textAnchor="middle" fontSize="10">🧪</text>

                  {/* DevOps (bottom-right) */}
                  <circle cx="150" cy="135" r="10" fill="var(--bg-elevated)" stroke="rgba(34,211,238,0.3)" strokeWidth="1"/>
                  <text x="150" y="139" textAnchor="middle" fontSize="10">🚀</text>
                </svg>

                <p className="text-sm font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
                  Agents ready
                </p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Send a task to activate the network
                </p>
              </div>
            )}
          </div>
        )}

        {/* Expanded agent detail */}
        {expanded && (
          <div className="mt-3 rounded-xl p-4 animate-[slideUp_0.25s_ease-out] mx-1"
            style={{ background: 'var(--bg-card)', border: stateStyles(expanded.state, expanded.name).border }}>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center text-lg"
                style={{ background: stateStyles(expanded.state, expanded.name).bgTint }}>
                {AGENT_ICONS[expanded.name] || '🔧'}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
                  {AGENT_LABELS[expanded.name] || expanded.name}
                </h3>
                <span className="text-[10px] font-bold tracking-[0.08em]"
                  style={{ color: stateStyles(expanded.state, expanded.name).labelColor, fontFamily: 'var(--font-mono)' }}>
                  {stateStyles(expanded.state, expanded.name).label}
                </span>
              </div>
              <div className="flex items-center gap-1">
                {/* Solo button */}
                <button
                  onClick={(e) => { e.stopPropagation(); setSoloAgent(soloAgent === expanded.name ? null : expanded.name); }}
                  className="p-1.5 rounded-lg transition-all active:scale-90"
                  style={{ color: soloAgent === expanded.name ? getAgentAccent(expanded.name).color : 'var(--text-muted)', background: soloAgent === expanded.name ? getAgentAccent(expanded.name).bg : 'transparent' }}
                  title="Solo mode"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                  </svg>
                </button>
                <button onClick={() => setExpandedAgent(null)}
                  className="p-1.5 rounded-lg transition-all active:scale-90"
                  style={{ color: 'var(--text-muted)' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M18 6L6 18M6 6l12 12"/>
                  </svg>
                </button>
              </div>
            </div>
            {expanded.task && <p className="text-xs mb-3 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{expanded.task}</p>}
            {(expanded.state === 'working' || expanded.state === 'waiting') && expanded.current_tool && (
              <ToolActivity tool={expanded.current_tool} agentName={expanded.name} />
            )}
            {(expanded.state === 'done' || expanded.state === 'error') && expanded.last_result && (
              <div className="text-[11px] rounded-lg px-3 py-2 mb-2.5 whitespace-pre-wrap"
                style={{
                  background: expanded.state === 'done' ? 'rgba(61,214,140,0.04)' : 'rgba(245,71,91,0.04)',
                  color: expanded.state === 'done' ? '#3dd68c' : '#f5475b',
                  maxHeight: '200px', overflowY: 'auto',
                }}>
                {expanded.last_result.replace(/\*\w+\*\s*/, '').slice(0, 500)}
              </div>
            )}
            <AgentStats agent={expanded} />
          </div>
        )}
      </div>
    );
  }

  // === GRID LAYOUT (Desktop) ===
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {subAgents.map((agent, index) => {
        const s = stateStyles(agent.state, agent.name);
        const icon = AGENT_ICONS[agent.name] || '🔧';
        const label = AGENT_LABELS[agent.name] || agent.name;
        const isExpanded = expandedAgent === agent.name;
        const isSelected = selectedAgent === agent.name;
        const recentDelegation = isRecentDelegation(agent);
        const accent = getAgentAccent(agent.name);

        return (
          <div
            key={agent.name}
            className={`relative rounded-xl transition-all duration-300 cursor-pointer card-hover overflow-hidden
              ${recentDelegation ? 'animate-[delegationPulse_1.5s_ease-out]' : ''}
              ${isSelected ? 'ring-1 ring-[var(--accent-blue)]/30' : ''}
              ${(agent.state === 'working' || agent.state === 'waiting') ? 'agent-card-working' : ''}`}
            style={{
              background: 'var(--bg-card)', border: s.border, boxShadow: s.boxShadow,
              borderLeft: `3px solid ${accent.color}${agent.state === 'idle' ? '15' : '60'}`,
              animation: `slideUp 0.3s ease-out ${index * 50}ms backwards`,
            }}
            onClick={() => { if (onSelectAgent) onSelectAgent(agent.name); setExpandedAgent(isExpanded ? null : agent.name); }}
          >
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
            <div className="p-4 relative z-10">
              <div className="flex items-center gap-3 mb-3">
                <div className="relative flex-shrink-0">
                  <div className="w-11 h-11 rounded-xl flex items-center justify-center text-lg transition-all duration-500" style={{ background: s.bgTint }}>{icon}</div>
                  <div className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[var(--bg-card)] ${s.pulse ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: s.dotColor }} />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-[13px] font-bold" style={{ color: 'var(--text-primary)' }}>{label}</h3>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9px] font-bold tracking-[0.1em]" style={{ color: s.labelColor, fontFamily: 'var(--font-mono)' }}>{s.label}</span>
                    {(agent.state === 'working' || agent.state === 'waiting') && (() => {
                      const elapsed = agent.started_at ? Math.round((Date.now() - agent.started_at) / 1000) : (agent.duration > 0 ? Math.round(agent.duration) : 0);
                      const isStale = agent.last_update_at ? (Date.now() - agent.last_update_at) > 90000 : (agent.started_at ? (Date.now() - agent.started_at) > 90000 : false);
                      return (
                        <>
                          {elapsed > 0 && (
                            <span className="text-[9px]" style={{ color: isStale ? 'var(--accent-amber)' : 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                              {elapsed >= 60 ? `${Math.floor(elapsed / 60)}m${elapsed % 60}s` : `${elapsed}s`}
                            </span>
                          )}
                          {isStale && (
                            <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full" style={{
                              background: 'rgba(245,166,35,0.1)', color: 'var(--accent-amber)',
                              border: '1px solid rgba(245,166,35,0.2)', fontFamily: 'var(--font-mono)',
                            }}>
                              THINKING
                            </span>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>
              </div>
              {agent.task && (
                <p className={`text-xs mb-2.5 leading-relaxed`} style={{ color: (agent.state === 'working' || agent.state === 'waiting') ? 'var(--text-secondary)' : 'var(--text-muted)' }}>
                  {agent.task.length > 120 ? agent.task.slice(0, 120) + '…' : agent.task}
                </p>
              )}
              {(agent.state === 'working' || agent.state === 'waiting') && agent.current_tool && <ToolActivity tool={agent.current_tool} agentName={agent.name} />}
              {(agent.state === 'done' || agent.state === 'error') && agent.last_result && (
                <div className="text-[11px] rounded-lg px-3 py-2 mb-2.5 truncate"
                  style={{ background: agent.state === 'done' ? 'rgba(61,214,140,0.04)' : 'rgba(245,71,91,0.04)', color: agent.state === 'done' ? '#3dd68c99' : '#f5475b99' }}>
                  {agent.last_result.replace(/\*\w+\*\s*/, '').slice(0, 120)}
                </div>
              )}
              {agent.state === 'idle' && !agent.task && <p className="text-xs italic" style={{ color: 'var(--text-muted)' }}>Ready for tasks</p>}
              {(agent.state === 'working' || agent.state === 'waiting') && (
                <div className="h-[2px] rounded-full overflow-hidden mt-3" style={{ background: 'var(--border-dim)' }}>
                  <div className="h-full rounded-full animate-[loading_2s_ease-in-out_infinite]"
                    style={{ width: '60%', background: `linear-gradient(90deg, ${accent.color}, ${accent.color}80)` }} />
                </div>
              )}
              <AgentStats agent={agent} />
            </div>
            {isExpanded && agent.task && (
              <div className="px-4 pb-4 relative z-10" style={{ borderTop: '1px solid var(--border-dim)' }}>
                <p className="text-xs mt-3 whitespace-pre-wrap" style={{ color: 'var(--text-secondary)' }}>{agent.task}</p>
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
  const accent = getAgentAccent(agentName);
  return (
    <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 mb-2.5" style={{ background: `${accent.color}08` }}>
      <div className="flex gap-[3px] flex-shrink-0">
        {[0, 1, 2].map(i => (
          <span key={i} className="w-[5px] h-[5px] rounded-full animate-bounce"
            style={{ backgroundColor: `${accent.color}90`, animationDelay: `${i * 150}ms`, animationDuration: '0.8s' }} />
        ))}
      </div>
      <span className="text-[11px] truncate text-fade-right" style={{ color: `${accent.color}cc`, fontFamily: 'var(--font-mono)' }}>
        {tool}
      </span>
    </div>
  );
}

/** Telemetry-style stats row */
function AgentStats({ agent }: { agent: AgentState }) {
  if (agent.turns <= 0 && agent.duration <= 0) return null;
  return (
    <div className="flex items-center gap-3 mt-3 pt-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
      {agent.turns > 0 && <span className="telemetry" style={{ color: 'var(--text-muted)' }}>{agent.turns} turns</span>}
      {agent.duration > 0 && <span className="telemetry" style={{ color: 'var(--text-muted)' }}>{Math.round(agent.duration)}s</span>}
    </div>
  );
}
