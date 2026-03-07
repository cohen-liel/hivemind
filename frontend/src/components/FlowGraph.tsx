import { useMemo } from 'react';
import type { AgentState } from '../types';
import { AGENT_ICONS } from '../constants';

interface Props {
  agents: AgentState[];
  onSelectAgent?: (name: string) => void;
}

const AGENT_COLORS: Record<string, { stroke: string; fill: string; text: string; glow: string }> = {
  orchestrator: { stroke: '#6b7280', fill: '#1f2937', text: '#d1d5db', glow: 'rgba(107,114,128,0.3)' },
  developer: { stroke: '#3b82f6', fill: '#1e3a5f', text: '#93c5fd', glow: 'rgba(59,130,246,0.4)' },
  reviewer: { stroke: '#a855f7', fill: '#3b1f5e', text: '#c4b5fd', glow: 'rgba(168,85,247,0.4)' },
  tester: { stroke: '#f59e0b', fill: '#4a3419', text: '#fcd34d', glow: 'rgba(245,158,11,0.4)' },
  devops: { stroke: '#06b6d4', fill: '#164e63', text: '#67e8f9', glow: 'rgba(6,182,212,0.4)' },
};

function stateColor(state: string): string {
  switch (state) {
    case 'working': return '#3b82f6';
    case 'done': return '#22c55e';
    case 'error': return '#ef4444';
    default: return '#4b5563';
  }
}

export default function FlowGraph({ agents, onSelectAgent }: Props) {
  const orchestrator = agents.find(a => a.name === 'orchestrator');
  const subAgents = agents.filter(a => a.name !== 'orchestrator');

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const agentKey = subAgents.map(a => a.name).join(',');

  // Calculate positions in a radial layout
  const layout = useMemo(() => {
    const cx = 200;
    const cy = 160;
    const radius = 110;
    const positions: Record<string, { x: number; y: number }> = {
      orchestrator: { x: cx, y: cy },
    };
    subAgents.forEach((agent, i) => {
      const angle = (i / Math.max(subAgents.length, 1)) * Math.PI * 2 - Math.PI / 2;
      positions[agent.name] = {
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
      };
    });
    return positions;
  }, [agentKey]); // recalc when agent set changes

  return (
    <div className="w-full flex justify-center">
      <svg viewBox="0 0 400 320" className="w-full max-w-[400px]" style={{ filter: 'drop-shadow(0 0 8px rgba(0,0,0,0.3))' }}>
        <defs>
          {/* Arrow marker */}
          <marker id="arrow" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
            <polygon points="0 0, 10 3.5, 0 7" fill="#4b5563" />
          </marker>
          <marker id="arrow-active" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
            <polygon points="0 0, 10 3.5, 0 7" fill="#3b82f6" />
          </marker>
        </defs>

        {/* Connection lines from orchestrator to sub-agents */}
        {subAgents.map(agent => {
          const from = layout.orchestrator;
          const to = layout[agent.name];
          if (!from || !to) return null;
          const isActive = agent.state === 'working';
          const isDelegating = agent.delegated_from === 'orchestrator' && agent.delegated_at && (Date.now() - agent.delegated_at < 5000);

          return (
            <g key={`line-${agent.name}`}>
              <line
                x1={from.x} y1={from.y}
                x2={to.x} y2={to.y}
                stroke={isActive ? '#3b82f6' : '#374151'}
                strokeWidth={isActive ? 2 : 1}
                strokeDasharray={isActive ? '6 3' : 'none'}
                markerEnd={isActive ? 'url(#arrow-active)' : 'url(#arrow)'}
                className={isDelegating ? 'animate-[dashFlow_1s_linear_infinite]' : ''}
              />
              {/* Animated dot traveling along the line when active */}
              {isActive && (
                <circle r="3" fill="#3b82f6" opacity="0.8">
                  <animateMotion
                    dur="1.5s"
                    repeatCount="indefinite"
                    path={`M${from.x},${from.y} L${to.x},${to.y}`}
                  />
                </circle>
              )}
            </g>
          );
        })}

        {/* Orchestrator node (center) */}
        {orchestrator && (() => {
          const pos = layout.orchestrator;
          const colors = AGENT_COLORS.orchestrator;
          const isWorking = orchestrator.state === 'working';
          return (
            <g
              onClick={() => onSelectAgent?.('orchestrator')}
              className="cursor-pointer"
            >
              {isWorking && (
                <circle cx={pos.x} cy={pos.y} r="32" fill="none" stroke="#3b82f6" strokeWidth="1.5" opacity="0.3">
                  <animate attributeName="r" values="28;36;28" dur="2s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.3;0.1;0.3" dur="2s" repeatCount="indefinite" />
                </circle>
              )}
              <circle cx={pos.x} cy={pos.y} r="28"
                fill={colors.fill} stroke={stateColor(orchestrator.state)} strokeWidth="2.5" />
              <text x={pos.x} y={pos.y + 1} textAnchor="middle" dominantBaseline="central" fontSize="20">
                {AGENT_ICONS.orchestrator}
              </text>
              <text x={pos.x} y={pos.y + 42} textAnchor="middle" fill={colors.text} fontSize="10" fontWeight="600">
                Orchestrator
              </text>
              {/* State badge */}
              <circle cx={pos.x + 20} cy={pos.y - 20} r="5" fill={stateColor(orchestrator.state)} />
            </g>
          );
        })()}

        {/* Sub-agent nodes */}
        {subAgents.map(agent => {
          const pos = layout[agent.name];
          if (!pos) return null;
          const colors = AGENT_COLORS[agent.name] || AGENT_COLORS.orchestrator;
          const icon = AGENT_ICONS[agent.name] || '\u{1F527}';
          const isWorking = agent.state === 'working';

          return (
            <g
              key={agent.name}
              onClick={() => onSelectAgent?.(agent.name)}
              className="cursor-pointer"
            >
              {/* Working pulse ring */}
              {isWorking && (
                <circle cx={pos.x} cy={pos.y} r="28" fill="none" stroke={colors.stroke} strokeWidth="1.5" opacity="0.3">
                  <animate attributeName="r" values="24;32;24" dur="2s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.4;0.1;0.4" dur="2s" repeatCount="indefinite" />
                </circle>
              )}
              <circle cx={pos.x} cy={pos.y} r="24"
                fill={colors.fill} stroke={stateColor(agent.state)} strokeWidth="2" />
              <text x={pos.x} y={pos.y + 1} textAnchor="middle" dominantBaseline="central" fontSize="18">
                {icon}
              </text>
              <text x={pos.x} y={pos.y + 36} textAnchor="middle" fill={colors.text} fontSize="9" fontWeight="600">
                {agent.name.charAt(0).toUpperCase() + agent.name.slice(1)}
              </text>
              {/* State badge */}
              <circle cx={pos.x + 17} cy={pos.y - 17} r="4" fill={stateColor(agent.state)} />
              {/* Current tool text */}
              {isWorking && agent.current_tool && (
                <text x={pos.x} y={pos.y + 48} textAnchor="middle" fill="#93c5fd" fontSize="7" opacity="0.7">
                  {agent.current_tool.slice(0, 25)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
