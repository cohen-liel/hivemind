import { useMemo } from 'react';
import type { ActivityEntry } from '../types';

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
        // Match numbered lines: "1. Do X" or "1) Do X" or "- Step: Do X"
        const match = line.match(/^\s*(?:(\d+)[.)]\s+|[-*]\s+)(.+)/);
        if (match) {
          const text = match[2].trim();
          if (text.length < 10 || text.length > 200) continue;

          // Try to detect which agent is mentioned
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

          steps.push({
            index: steps.length + 1,
            text,
            status,
            agent,
          });
        }
      }
    }
  }

  // If we found plan steps from delegation events instead
  if (steps.length === 0) {
    for (const a of activities) {
      if (a.type === 'delegation' && a.to_agent && a.task) {
        const agent = a.to_agent;
        let status: PlanStep['status'] = 'pending';
        if (errorAgents.has(agent)) status = 'error';
        else if (finishedAgents.has(agent)) status = 'done';
        else if (workingAgents.has(agent)) status = 'in_progress';

        steps.push({
          index: steps.length + 1,
          text: a.task,
          status,
          agent,
        });
      }
    }
  }

  return steps;
}

const STATUS_ICONS = {
  pending: (
    <div className="w-5 h-5 rounded-full border-2 border-gray-700 flex-shrink-0" />
  ),
  in_progress: (
    <div className="w-5 h-5 rounded-full border-2 border-blue-500 flex items-center justify-center flex-shrink-0">
      <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
    </div>
  ),
  done: (
    <div className="w-5 h-5 rounded-full bg-green-500/20 border-2 border-green-500 flex items-center justify-center flex-shrink-0">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#22c55e" strokeWidth="2.5" strokeLinecap="round">
        <path d="M3 8l3.5 3.5L13 5" />
      </svg>
    </div>
  ),
  error: (
    <div className="w-5 h-5 rounded-full bg-red-500/20 border-2 border-red-500 flex items-center justify-center flex-shrink-0">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round">
        <path d="M4 4l8 8M12 4l-8 8" />
      </svg>
    </div>
  ),
};

const AGENT_BADGE_COLORS: Record<string, string> = {
  developer: 'bg-blue-500/20 text-blue-400',
  reviewer: 'bg-purple-500/20 text-purple-400',
  tester: 'bg-amber-500/20 text-amber-400',
  devops: 'bg-cyan-500/20 text-cyan-400',
};

export default function PlanView({ activities }: Props) {
  const steps = useMemo(() => extractPlan(activities), [activities]);

  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm px-4">
        <div className="w-12 h-12 rounded-full bg-gray-800 flex items-center justify-center mb-3 text-xl">
          {'\u{1F4CB}'}
        </div>
        <p className="font-medium text-gray-400">No plan detected</p>
        <p className="text-gray-600 text-xs mt-1">The orchestrator will show its plan here</p>
      </div>
    );
  }

  const completedCount = steps.filter(s => s.status === 'done').length;
  const pct = steps.length > 0 ? Math.round((completedCount / steps.length) * 100) : 0;

  return (
    <div className="p-4 space-y-3">
      {/* Progress header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1">
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-600 to-green-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <span className="text-xs text-gray-500 font-mono flex-shrink-0">
          {completedCount}/{steps.length}
        </span>
      </div>

      {/* Steps checklist */}
      {steps.map((step, i) => (
        <div
          key={i}
          className={`flex items-start gap-3 py-2 px-3 rounded-xl transition-all
            ${step.status === 'in_progress' ? 'bg-blue-500/5 border border-blue-500/20' : ''}
            ${step.status === 'done' ? 'opacity-70' : ''}
            ${step.status === 'error' ? 'bg-red-500/5 border border-red-500/20' : ''}`}
        >
          {STATUS_ICONS[step.status]}
          <div className="flex-1 min-w-0">
            <p className={`text-sm leading-relaxed
              ${step.status === 'done' ? 'text-gray-500 line-through' : 'text-gray-200'}`}>
              {step.text}
            </p>
            {step.agent && (
              <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded mt-1
                ${AGENT_BADGE_COLORS[step.agent] || 'bg-gray-800 text-gray-500'}`}>
                {step.agent}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
