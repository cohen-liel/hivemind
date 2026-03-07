import type { AgentState } from '../types';
import { useState } from 'react';

interface Props {
  agents: AgentState[];
  onSelectAgent?: (name: string) => void;
  selectedAgent?: string | null;
  layout?: 'grid' | 'compact' | 'bubbles';
}

const AGENT_ICONS: Record<string, string> = {
  orchestrator: '\u{1F3AF}',
  developer: '\u{1F4BB}',
  reviewer: '\u{1F50D}',
  tester: '\u{1F9EA}',
  devops: '\u{2699}\uFE0F',
};

const AGENT_LABELS: Record<string, string> = {
  developer: 'Developer',
  reviewer: 'Reviewer',
  tester: 'Tester',
  devops: 'DevOps',
  orchestrator: 'Orchestrator',
};

const AGENT_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  developer: { border: 'border-l-blue-500', bg: 'bg-blue-500', text: 'text-blue-400' },
  reviewer: { border: 'border-l-purple-500', bg: 'bg-purple-500', text: 'text-purple-400' },
  tester: { border: 'border-l-amber-500', bg: 'bg-amber-500', text: 'text-amber-400' },
  devops: { border: 'border-l-cyan-500', bg: 'bg-cyan-500', text: 'text-cyan-400' },
  orchestrator: { border: 'border-l-gray-500', bg: 'bg-gray-500', text: 'text-gray-400' },
};

function stateConfig(state: string) {
  switch (state) {
    case 'working':
      return {
        glow: 'shadow-[0_0_25px_rgba(59,130,246,0.5)] border-blue-500/60',
        bg: 'bg-blue-500/10',
        dotColor: 'bg-blue-500',
        dotPulse: true,
        label: 'Working',
        labelColor: 'text-blue-400',
        opacity: '',
      };
    case 'done':
      return {
        glow: 'shadow-[0_0_15px_rgba(34,197,94,0.25)] border-green-500/40',
        bg: 'bg-green-500/5',
        dotColor: 'bg-green-500',
        dotPulse: false,
        label: 'Done',
        labelColor: 'text-green-400',
        opacity: '',
      };
    case 'error':
      return {
        glow: 'shadow-[0_0_15px_rgba(239,68,68,0.35)] border-red-500/50 animate-[shake_0.5s_ease-in-out]',
        bg: 'bg-red-500/5',
        dotColor: 'bg-red-500',
        dotPulse: false,
        label: 'Error',
        labelColor: 'text-red-400',
        opacity: '',
      };
    default:
      return {
        glow: 'border-gray-800/60',
        bg: 'bg-gray-800/30',
        dotColor: 'bg-gray-600',
        dotPulse: false,
        label: 'Standby',
        labelColor: 'text-gray-600',
        opacity: 'opacity-50',
      };
  }
}

// Check if delegation happened recently (within 5 seconds)
function isRecentDelegation(agent: AgentState): boolean {
  if (!agent.delegated_at) return false;
  return Date.now() - agent.delegated_at < 5000;
}

export default function AgentStatusPanel({ agents, onSelectAgent, selectedAgent, layout = 'grid' }: Props) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  if (agents.length === 0) {
    return (
      <div className="text-gray-600 text-sm italic p-8 text-center">
        No agents registered yet
      </div>
    );
  }

  const subAgents = agents.filter(a => a.name !== 'orchestrator');

  if (layout === 'compact') {
    return (
      <div className="space-y-2 px-1">
        {subAgents.map((agent) => {
          const cfg = stateConfig(agent.state);
          const icon = AGENT_ICONS[agent.name] || '\u{1F527}';
          const recentDelegation = isRecentDelegation(agent);

          return (
            <div
              key={agent.name}
              className={`bg-gray-900/80 border rounded-lg p-2.5 transition-all duration-300
                ${recentDelegation ? 'animate-[delegationPulse_1s_ease-out]' : ''}
                ${cfg.glow} ${cfg.opacity}`}
            >
              <div className="flex items-center gap-2.5">
                <div className="relative flex-shrink-0">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${cfg.bg}`}>
                    {icon}
                  </div>
                  <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-gray-900 ${cfg.dotColor} ${cfg.dotPulse ? 'animate-pulse' : ''}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-gray-300 capitalize">{agent.name}</span>
                    <span className={`text-[9px] font-medium uppercase ${cfg.labelColor}`}>{cfg.label}</span>
                  </div>
                  {agent.state === 'working' && agent.current_tool && (
                    <p className="text-[10px] text-blue-300/70 font-mono truncate mt-0.5">{agent.current_tool}</p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // === BUBBLES LAYOUT: Compact circular avatars for mobile ===
  if (layout === 'bubbles') {
    const expanded = expandedAgent ? subAgents.find(a => a.name === expandedAgent) : null;

    return (
      <div>
        {/* Bubbles grid */}
        <div className="flex flex-wrap justify-center gap-4 py-2">
          {subAgents.map((agent) => {
            const cfg = stateConfig(agent.state);
            const icon = AGENT_ICONS[agent.name] || '\u{1F527}';
            const label = AGENT_LABELS[agent.name] || agent.name;
            const isSelected = expandedAgent === agent.name;
            const recentDelegation = isRecentDelegation(agent);

            return (
              <button
                key={agent.name}
                onClick={() => setExpandedAgent(isSelected ? null : agent.name)}
                className="flex flex-col items-center gap-1.5 group"
              >
                <div className={`relative transition-all duration-500
                  ${recentDelegation ? 'animate-[delegationPulse_1.5s_ease-out]' : ''}`}>
                  <div className={`w-14 h-14 rounded-full flex items-center justify-center text-2xl transition-all duration-500
                    border-2 ${isSelected ? 'scale-110' : ''}
                    ${agent.state === 'working'
                      ? 'bg-blue-500/15 border-blue-500 shadow-[0_0_20px_rgba(59,130,246,0.5)]'
                      : agent.state === 'done'
                        ? 'bg-green-500/10 border-green-500/50 shadow-[0_0_12px_rgba(34,197,94,0.25)]'
                        : agent.state === 'error'
                          ? 'bg-red-500/10 border-red-500/50 shadow-[0_0_12px_rgba(239,68,68,0.3)] animate-[shake_0.5s_ease-in-out]'
                          : 'bg-gray-800/50 border-gray-700/30 opacity-50'}`}>
                    {icon}
                  </div>
                  {/* State dot */}
                  <div className={`absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-gray-950
                    ${cfg.dotColor} ${cfg.dotPulse ? 'animate-pulse' : ''}`} />
                  {/* Working spinner ring */}
                  {agent.state === 'working' && (
                    <div className="absolute inset-0 rounded-full border-2 border-blue-400/30 animate-ping" />
                  )}
                </div>
                <span className={`text-[10px] font-semibold transition-colors
                  ${agent.state === 'working' ? 'text-blue-400'
                    : agent.state === 'done' ? 'text-green-400'
                    : agent.state === 'error' ? 'text-red-400'
                    : 'text-gray-600'}`}>
                  {label}
                </span>
              </button>
            );
          })}
        </div>

        {/* Expanded agent detail panel */}
        {expanded && (
          <div className="mt-3 bg-gray-900/80 border border-gray-800/60 rounded-2xl p-4 animate-[fadeSlideIn_0.2s_ease-out]">
            <div className="flex items-center gap-3 mb-2">
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg
                ${stateConfig(expanded.state).bg}`}>
                {AGENT_ICONS[expanded.name] || '\u{1F527}'}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-bold text-gray-200">
                  {AGENT_LABELS[expanded.name] || expanded.name}
                </h3>
                <span className={`text-[11px] font-semibold uppercase ${stateConfig(expanded.state).labelColor}`}>
                  {stateConfig(expanded.state).label}
                </span>
              </div>
              <button
                onClick={() => setExpandedAgent(null)}
                className="text-gray-600 hover:text-gray-400 p-1"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
              </button>
            </div>

            {/* Task */}
            {expanded.task && (
              <p className="text-xs text-gray-400 mb-2 leading-relaxed">{expanded.task}</p>
            )}

            {/* Current tool */}
            {expanded.state === 'working' && expanded.current_tool && (
              <div className="flex items-center gap-2 bg-blue-500/10 rounded-lg px-3 py-2 mb-2">
                <div className="flex gap-0.5 flex-shrink-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
                <span className="text-xs text-blue-300/90 font-mono truncate">{expanded.current_tool}</span>
              </div>
            )}

            {/* Last result */}
            {(expanded.state === 'done' || expanded.state === 'error') && expanded.last_result && (
              <div className={`text-[11px] rounded-lg px-3 py-2 mb-2
                ${expanded.state === 'done' ? 'bg-green-500/5 text-green-300/70' : 'bg-red-500/5 text-red-300/70'}`}>
                {expanded.last_result.replace(/\*\w+\*\s*/, '').slice(0, 150)}
              </div>
            )}

            {/* Stats */}
            {(expanded.cost > 0 || expanded.turns > 0 || expanded.duration > 0) && (
              <div className="flex items-center gap-3 pt-2 border-t border-gray-800/40">
                {expanded.cost > 0 && <span className="text-[11px] text-gray-500 font-mono">${expanded.cost.toFixed(3)}</span>}
                {expanded.turns > 0 && <span className="text-[11px] text-gray-600">{expanded.turns} turns</span>}
                {expanded.duration > 0 && <span className="text-[11px] text-gray-600">{Math.round(expanded.duration)}s</span>}
              </div>
            )}

            {/* Loading bar */}
            {expanded.state === 'working' && (
              <div className="h-1 bg-gray-800 rounded-full overflow-hidden mt-2">
                <div className="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full animate-[loading_2s_ease-in-out_infinite]"
                  style={{ width: '60%' }} />
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {subAgents.map((agent) => {
        const cfg = stateConfig(agent.state);
        const icon = AGENT_ICONS[agent.name] || '\u{1F527}';
        const label = AGENT_LABELS[agent.name] || agent.name;
        const isExpanded = expandedAgent === agent.name;
        const isSelected = selectedAgent === agent.name;
        const recentDelegation = isRecentDelegation(agent);
        const agentColor = AGENT_COLORS[agent.name];

        return (
          <div
            key={agent.name}
            className={`relative bg-gray-900/80 border rounded-2xl transition-all duration-500 cursor-pointer
              ${agentColor ? `border-l-[3px] ${agentColor.border}` : ''}
              ${recentDelegation ? 'animate-[delegationPulse_1.5s_ease-out]' : ''}
              ${cfg.glow} ${cfg.opacity} ${isSelected ? 'ring-1 ring-blue-500/40' : ''}
              hover:border-gray-700/80`}
            onClick={() => {
              if (onSelectAgent) onSelectAgent(agent.name);
              setExpandedAgent(isExpanded ? null : agent.name);
            }}
          >
            {/* Delegation banner */}
            {recentDelegation && agent.delegated_from && (
              <div className="px-4 pt-3 pb-0 animate-[fadeSlideIn_0.3s_ease-out]">
                <div className="flex items-center gap-1.5 text-[10px] text-blue-400 bg-blue-500/10 rounded-lg px-2.5 py-1.5">
                  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M1 8h10M8 4l4 4-4 4"/>
                  </svg>
                  Task from <span className="font-semibold capitalize">{agent.delegated_from}</span>
                </div>
              </div>
            )}

            {/* Card content */}
            <div className="p-4">
              {/* Header: icon + name + state */}
              <div className="flex items-center gap-3 mb-3">
                <div className="relative flex-shrink-0">
                  <div className={`w-12 h-12 rounded-xl flex items-center justify-center text-xl transition-all duration-500 ${cfg.bg}`}>
                    {icon}
                  </div>
                  <div className={`absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-gray-900
                    ${cfg.dotColor} ${cfg.dotPulse ? 'animate-pulse' : ''}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-bold text-gray-200">{label}</h3>
                  <span className={`text-[11px] font-semibold uppercase tracking-wide ${cfg.labelColor}`}>
                    {cfg.label}
                  </span>
                </div>
              </div>

              {/* Task description */}
              {agent.task && (
                <p className={`text-xs mb-2 leading-relaxed ${agent.state === 'working' ? 'text-gray-300' : 'text-gray-500'}`}>
                  {agent.task.length > 120 ? agent.task.slice(0, 120) + '...' : agent.task}
                </p>
              )}

              {/* Current tool (thought bubble) */}
              {agent.state === 'working' && agent.current_tool && (
                <div className="flex items-center gap-2 bg-blue-500/10 rounded-lg px-3 py-2 mb-2">
                  <div className="flex gap-0.5 flex-shrink-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400/80 animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                  <span className="text-xs text-blue-300/90 font-mono truncate">{agent.current_tool}</span>
                </div>
              )}

              {/* Last result preview (for done/error agents) */}
              {(agent.state === 'done' || agent.state === 'error') && agent.last_result && (
                <div className={`text-[11px] rounded-lg px-3 py-2 mb-2 truncate
                  ${agent.state === 'done'
                    ? 'bg-green-500/5 text-green-300/70'
                    : 'bg-red-500/5 text-red-300/70'}`}>
                  {agent.last_result.replace(/\*\w+\*\s*/, '').slice(0, 120)}
                </div>
              )}

              {/* No task placeholder for idle */}
              {agent.state === 'idle' && !agent.task && (
                <p className="text-xs text-gray-700 italic">Ready for tasks</p>
              )}

              {/* Loading bar for working agents */}
              {agent.state === 'working' && (
                <div className="h-1 bg-gray-800 rounded-full overflow-hidden mt-2">
                  <div className="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full animate-[loading_2s_ease-in-out_infinite]"
                    style={{ width: '60%' }} />
                </div>
              )}

              {/* Stats bar */}
              {(agent.cost > 0 || agent.turns > 0 || agent.duration > 0) && (
                <div className="flex items-center gap-3 mt-3 pt-2 border-t border-gray-800/40">
                  {agent.cost > 0 && (
                    <span className="text-[11px] text-gray-500 font-mono">${agent.cost.toFixed(3)}</span>
                  )}
                  {agent.turns > 0 && (
                    <span className="text-[11px] text-gray-600">{agent.turns} turns</span>
                  )}
                  {agent.duration > 0 && (
                    <span className="text-[11px] text-gray-600">{Math.round(agent.duration)}s</span>
                  )}
                </div>
              )}
            </div>

            {/* Expanded full details */}
            {isExpanded && agent.task && (
              <div className="px-4 pb-4 border-t border-gray-800/30">
                <p className="text-xs text-gray-400 mt-3 whitespace-pre-wrap">{agent.task}</p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
