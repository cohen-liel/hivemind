import { useMemo } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

interface Props {
  activities: ActivityEntry[];
}

interface PlanStep {
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done' | 'error';
  agent?: string;
}

/**
 * Parses orchestrator output for numbered steps and tracks their completion
 * based on agent_finished events.
 */
function extractPlan(activities: ActivityEntry[]): PlanStep[] {
  const steps: PlanStep[] = [];
  const finishedAgents = new Set<string>();
  const errorAgents = new Set<string>();
  const workingAgents = new Set<string>();

  // Collect agent states
  for (const a of activities) {
    if (a.type === 'agent_finished' && a.agent) {
      if (a.is_error) errorAgents.add(a.agent);
      else finishedAgents.add(a.agent);
      workingAgents.delete(a.agent);
    }
    if (a.type === 'agent_started' && a.agent) {
      workingAgents.add(a.agent);
    }
  }

  // Find agent_text from orchestrator that looks like a plan
  for (const a of activities) {
    if (a.type === 'agent_text' && a.agent === 'orchestrator' && a.content) {
      const lines = a.content.split('\n');
      for (const line of lines) {
        const match = line.match(/^\s*(?:(\d+)[.)]\s+|[-*]\s+)(.+)/);
        if (match) {
          const text = match[2].trim();
          if (text.length < 10 || text.length > 200) continue;

          let agent: string | undefined;
          const lowerText = text.toLowerCase();
          if (lowerText.includes('develop') || lowerText.includes('implement') || lowerText.includes('code') || lowerText.includes('write')) {
            agent = 'developer';
          } else if (lowerText.includes('review') || lowerText.includes('check')) {
            agent = 'reviewer';
          } else if (lowerText.includes('test')) {
            agent = 'tester';
          } else if (lowerText.includes('deploy') || lowerText.includes('docker') || lowerText.includes('ci/cd')) {
            agent = 'devops';
          }

          let status: PlanStep['status'] = 'pending';
          if (agent) {
            if (errorAgents.has(agent)) status = 'error';
            else if (finishedAgents.has(agent)) status = 'done';
            else if (workingAgents.has(agent)) status = 'in_progress';
          }

          steps.push({ index: steps.length + 1, text, status, agent });
        }
      }
    }
  }

  // Fallback: delegation events
  if (steps.length === 0) {
    for (const a of activities) {
      if (a.type === 'delegation' && a.to_agent && a.task) {
        const agent = a.to_agent;
        let status: PlanStep['status'] = 'pending';
        if (errorAgents.has(agent)) status = 'error';
        else if (finishedAgents.has(agent)) status = 'done';
        else if (workingAgents.has(agent)) status = 'in_progress';

        steps.push({ index: steps.length + 1, text: a.task, status, agent });
      }
    }
  }

  return steps;
}

function StatusIcon({ status, agentName }: { status: PlanStep['status']; agentName?: string }) {
  const accent = agentName ? getAgentAccent(agentName) : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };

  switch (status) {
    case 'done':
      return (
        <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 transition-all"
          style={{ background: 'rgba(61,214,140,0.12)', border: '2px solid var(--accent-green)' }}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="var(--accent-green)" strokeWidth="2.5" strokeLinecap="round">
            <path d="M3 8l3.5 3.5L13 5" />
          </svg>
        </div>
      );
    case 'in_progress':
      return (
        <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 relative"
          style={{ background: accent.bg, border: `2px solid ${accent.color}`, boxShadow: `0 0 12px ${accent.glow}` }}>
          <div className="w-2.5 h-2.5 rounded-full animate-pulse" style={{ background: accent.color }} />
          {/* Spinning ring */}
          <div className="absolute inset-[-4px] rounded-full animate-[orbitalSpin_2s_linear_infinite]"
            style={{ border: `1px dashed ${accent.color}40` }} />
        </div>
      );
    case 'error':
      return (
        <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: 'var(--glow-red)', border: '2px solid var(--accent-red)' }}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="var(--accent-red)" strokeWidth="2.5" strokeLinecap="round">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </div>
      );
    default:
      return (
        <div className="w-7 h-7 rounded-full flex-shrink-0"
          style={{ background: 'var(--bg-elevated)', border: '2px solid var(--border-subtle)' }} />
      );
  }
}

export default function PlanView({ activities }: Props) {
  const steps = useMemo(() => extractPlan(activities), [activities]);

  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
          📋
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No plan detected</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
          The orchestrator will show its execution plan here
        </p>
      </div>
    );
  }

  const completedCount = steps.filter(s => s.status === 'done').length;
  const pct = steps.length > 0 ? Math.round((completedCount / steps.length) * 100) : 0;
  const activeStep = steps.find(s => s.status === 'in_progress');

  return (
    <div className="p-4">
      {/* Progress header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Execution Plan
            </span>
            <span className="text-xs font-bold"
              style={{ color: pct === 100 ? 'var(--accent-green)' : 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
              {pct}%
            </span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
            <div
              className="h-full rounded-full transition-all duration-700 ease-out relative"
              style={{
                width: `${pct}%`,
                background: pct === 100
                  ? 'var(--accent-green)'
                  : 'linear-gradient(90deg, var(--accent-blue), var(--accent-cyan))',
                boxShadow: pct > 0 ? `0 0 8px ${pct === 100 ? 'var(--glow-green)' : 'var(--glow-blue)'}` : 'none',
              }}
            >
              {/* Shimmer effect */}
              {pct > 0 && pct < 100 && (
                <div className="absolute inset-0 overflow-hidden rounded-full">
                  <div className="absolute inset-0 animate-[loading_2s_ease-in-out_infinite]"
                    style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent)' }} />
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="flex-shrink-0 text-xs px-2.5 py-1 rounded-lg"
          style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
          {completedCount}/{steps.length}
        </div>
      </div>

      {/* Timeline steps */}
      <div className="relative">
        {/* Vertical timeline line */}
        <div className="absolute left-[13px] top-4 bottom-4 w-[2px]"
          style={{ background: 'var(--border-subtle)' }} />

        <div className="space-y-0">
          {steps.map((step, i) => {
            const accent = step.agent ? getAgentAccent(step.agent) : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };
            const isActive = step.status === 'in_progress';
            const isDone = step.status === 'done';
            const isError = step.status === 'error';

            return (
              <div
                key={i}
                className={`flex items-start gap-3 relative py-2.5 transition-all duration-300
                  ${isActive ? 'animate-[fadeSlideIn_0.3s_ease-out]' : ''}`}
                style={{
                  opacity: isDone ? 0.6 : 1,
                }}
              >
                {/* Status icon (on the timeline line) */}
                <div className="relative z-10">
                  <StatusIcon status={step.status} agentName={step.agent} />
                </div>

                {/* Content card */}
                <div className={`flex-1 min-w-0 rounded-xl px-3.5 py-2.5 transition-all duration-300`}
                  style={{
                    background: isActive ? accent.bg : isError ? 'var(--glow-red)' : 'transparent',
                    border: isActive ? `1px solid ${accent.color}30` : isError ? '1px solid rgba(245,71,91,0.15)' : '1px solid transparent',
                    boxShadow: isActive ? `0 0 15px ${accent.glow}` : 'none',
                  }}>
                  <p className={`text-sm leading-relaxed ${isDone ? 'line-through' : ''}`}
                    style={{
                      color: isDone ? 'var(--text-muted)' : isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                    }}>
                    {step.text}
                  </p>

                  {/* Agent badge */}
                  {step.agent && (
                    <div className="flex items-center gap-1.5 mt-1.5">
                      <span className="text-xs">{AGENT_ICONS[step.agent] || '🔧'}</span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{
                          background: accent.bg,
                          color: accent.color,
                          border: `1px solid ${accent.color}20`,
                        }}>
                        {AGENT_LABELS[step.agent] || step.agent}
                      </span>
                      {isActive && (
                        <span className="text-[9px] font-bold tracking-wider animate-pulse"
                          style={{ color: accent.color, fontFamily: 'var(--font-mono)' }}>
                          WORKING
                        </span>
                      )}
                      {isDone && (
                        <span className="text-[9px] font-bold tracking-wider"
                          style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>
                          DONE
                        </span>
                      )}
                      {isError && (
                        <span className="text-[9px] font-bold tracking-wider"
                          style={{ color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
                          FAILED
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Active step highlight at bottom */}
      {activeStep && activeStep.agent && (
        <div className="mt-4 pt-3 flex items-center gap-2"
          style={{ borderTop: '1px solid var(--border-dim)' }}>
          <div className="w-2 h-2 rounded-full animate-pulse"
            style={{ background: getAgentAccent(activeStep.agent).color }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {AGENT_LABELS[activeStep.agent] || activeStep.agent} is working on step {activeStep.index}
          </span>
        </div>
      )}
    </div>
  );
}
