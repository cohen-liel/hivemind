/**
 * PipelinePhases — Visual pipeline progress indicator.
 *
 * Shows the orchestrator's current phase during task startup:
 *   Loading Context → Architect Review → PM Planning → Executing DAG → Memory Update
 *
 * Each phase is derived from the orchestrator's `task` field in agent_update events.
 * Phases animate in with a smooth transition and show elapsed time.
 */

import React, { useMemo } from 'react';
import type { AgentState } from '../types';

interface PipelinePhasesProps {
  orchestrator: AgentState | null;
  status: string;
  now: number;
}

interface Phase {
  id: string;
  label: string;
  /** Shortened label for narrow screens (≤400px) */
  shortLabel: string;
  icon: string;
  /** Keywords to match against orchestrator.task to detect this phase */
  keywords: string[];
}

const PHASES: Phase[] = [
  {
    id: 'context',
    label: 'Loading Context',
    shortLabel: 'Context',
    icon: '📚',
    keywords: ['loading project context', 'reading memory', 'loading context'],
  },
  {
    id: 'architect',
    label: 'Architecture Review',
    shortLabel: 'Architect',
    icon: '🏗️',
    keywords: ['architect', 'reviewing codebase', 'analysing architecture'],
  },
  {
    id: 'planning',
    label: 'PM Planning',
    shortLabel: 'Planning',
    icon: '📋',
    keywords: ['pm creating', 'planning', 'creating task graph', 'pm agent'],
  },
  {
    id: 'review',
    label: 'Plan Review',
    shortLabel: 'Review',
    icon: '🔍',
    keywords: ['critic', 'reviewing plan', 'plan check', 'evaluating'],
  },
  {
    id: 'executing',
    label: 'Executing Tasks',
    shortLabel: 'Execute',
    icon: '⚡',
    keywords: ['executing', 'dag executor', 'running tasks'],
  },
  {
    id: 'memory',
    label: 'Updating Memory',
    shortLabel: 'Memory',
    icon: '🧠',
    keywords: ['memory agent', 'updating project knowledge', 'memory updated'],
  },
];

function detectPhase(task: string | undefined): string | null {
  if (!task) return null;
  const lower = task.toLowerCase();
  for (const phase of PHASES) {
    if (phase.keywords.some(kw => lower.includes(kw))) {
      return phase.id;
    }
  }
  return null;
}

function getPhaseIndex(phaseId: string | null): number {
  if (!phaseId) return -1;
  return PHASES.findIndex(p => p.id === phaseId);
}

export const PipelinePhases = React.memo(function PipelinePhases({
  orchestrator,
  status,
  now,
}: PipelinePhasesProps): React.ReactElement | null {
  const isRunning = status === 'running';
  const orchTask = orchestrator?.task || orchestrator?.current_tool || '';

  const currentPhaseId = useMemo(() => detectPhase(orchTask), [orchTask]);
  const currentIndex = getPhaseIndex(currentPhaseId);

  // Don't show if not running or no phase detected
  if (!isRunning || currentIndex < 0) return null;

  // Calculate elapsed time for current phase
  const elapsedSec = orchestrator?.started_at
    ? Math.round((now - orchestrator.started_at) / 1000)
    : 0;

  return (
    <div
      className="pipeline-phases w-full animate-[fadeSlideIn_0.3s_ease-out]"
      style={{
        background: 'linear-gradient(180deg, rgba(99,140,255,0.03), transparent)',
        borderBottom: '1px solid var(--border-dim)',
      }}
    >
      {/* Phase steps */}
      <div className="pipeline-phases-track flex items-center justify-between gap-1">
        {PHASES.map((phase, i) => {
          const isActive = i === currentIndex;
          const isDone = i < currentIndex;
          const isFuture = i > currentIndex;

          return (
            <React.Fragment key={phase.id}>
              {/* Connector line */}
              {i > 0 && (
                <div
                  className="pipeline-connector flex-1 h-[2px] rounded-full transition-all duration-500"
                  style={{
                    background: isDone
                      ? 'var(--accent-green)'
                      : isActive
                      ? 'linear-gradient(90deg, var(--accent-green), var(--accent-blue))'
                      : 'var(--border-dim)',
                    opacity: isFuture ? 0.3 : 1,
                    minWidth: 4,
                  }}
                />
              )}
              {/* Phase dot */}
              <div
                className={`pipeline-phase-dot flex items-center justify-center flex-shrink-0 rounded-full transition-all duration-500 ${
                  isActive ? 'animate-pulse' : ''
                }`}
                style={{
                  width: isActive ? 28 : 20,
                  height: isActive ? 28 : 20,
                  background: isDone
                    ? 'rgba(61,214,140,0.15)'
                    : isActive
                    ? 'rgba(99,140,255,0.15)'
                    : 'var(--bg-elevated)',
                  border: `2px solid ${
                    isDone
                      ? 'var(--accent-green)'
                      : isActive
                      ? 'var(--accent-blue)'
                      : 'var(--border-subtle)'
                  }`,
                  boxShadow: isActive ? '0 0 12px rgba(99,140,255,0.3)' : 'none',
                  opacity: isFuture ? 0.35 : 1,
                }}
                title={phase.label}
              >
                <span className="pipeline-phase-icon" style={{ fontSize: isActive ? 12 : 9 }}>
                  {isDone ? '✓' : phase.icon}
                </span>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      {/* Current phase label + elapsed */}
      <div className="pipeline-phases-label flex items-center justify-center gap-2">
        <span
          className="pipeline-label-text text-[10px] font-bold tracking-wider"
          style={{
            color: 'var(--accent-blue)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          <span className="pipeline-label-icon">{PHASES[currentIndex]?.icon}{' '}</span>
          <span className="pipeline-label-full">{PHASES[currentIndex]?.label.toUpperCase()}</span>
          <span className="pipeline-label-short">{PHASES[currentIndex]?.shortLabel.toUpperCase()}</span>
        </span>
        {elapsedSec > 0 && (
          <span
            className="text-[9px] font-mono"
            style={{ color: 'var(--text-muted)' }}
          >
            {elapsedSec >= 60
              ? `${Math.floor(elapsedSec / 60)}m${elapsedSec % 60}s`
              : `${elapsedSec}s`}
          </span>
        )}
      </div>
    </div>
  );
});

export default PipelinePhases;
