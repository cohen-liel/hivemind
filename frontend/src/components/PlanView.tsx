import { useMemo, useState, useRef, useEffect } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';

// ============================================================================
// Types
// ============================================================================

interface DagTask {
  id: string;
  role: string;
  goal: string;
  depends_on?: string[];
  is_remediation?: boolean;
}

interface DagGraph {
  vision?: string;
  tasks?: DagTask[];
}

interface Props {
  activities: ActivityEntry[];
  dagGraph?: DagGraph | null;
  dagTaskStatus?: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>;
  dagTaskFailureReasons?: Record<string, string>;
}

interface PlanStep {
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done' | 'error' | 'cancelled';
  agent?: string;
  taskId?: string;
  dependsOn?: string[];
  failureReason?: string;
  isRemediation?: boolean;
}

// ============================================================================
// CSS Keyframes (injected once)
// ============================================================================

const STYLE_ID = 'planview-animations';

function ensureStyles(): void {
  if (typeof document === 'undefined') return;
  if (document.getElementById(STYLE_ID)) return;

  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    @keyframes planCheckDraw {
      0% { stroke-dashoffset: 20; opacity: 0; }
      30% { opacity: 1; }
      100% { stroke-dashoffset: 0; opacity: 1; }
    }
    @keyframes planCheckPop {
      0% { transform: scale(0.6); opacity: 0; }
      60% { transform: scale(1.15); }
      100% { transform: scale(1); opacity: 1; }
    }
    @keyframes planSpinRing {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    @keyframes planPulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    @keyframes planFadeSlideIn {
      from { opacity: 0; transform: translateY(-6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes planErrorShake {
      0%, 100% { transform: translateX(0); }
      20% { transform: translateX(-2px); }
      40% { transform: translateX(2px); }
      60% { transform: translateX(-1px); }
      80% { transform: translateX(1px); }
    }
    @keyframes planShimmer {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(200%); }
    }
    @keyframes planProgressGlow {
      0%, 100% { box-shadow: 0 0 4px var(--glow-blue); }
      50% { box-shadow: 0 0 12px var(--glow-blue); }
    }
    .plan-check-icon {
      animation: planCheckPop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
    }
    .plan-check-path {
      stroke-dasharray: 20;
      stroke-dashoffset: 20;
      animation: planCheckDraw 0.5s ease-out 0.15s forwards;
    }
    .plan-spinner-ring {
      animation: planSpinRing 1.2s linear infinite;
    }
    .plan-pulse {
      animation: planPulse 1.5s ease-in-out infinite;
    }
    .plan-fade-in {
      animation: planFadeSlideIn 0.3s ease-out forwards;
    }
    .plan-error-shake {
      animation: planErrorShake 0.4s ease-out;
    }
    .plan-shimmer {
      animation: planShimmer 2s ease-in-out infinite;
    }
    .plan-dep-line {
      stroke-dasharray: 4 3;
      opacity: 0.35;
      transition: opacity 0.3s;
    }
    .plan-step:hover .plan-dep-line {
      opacity: 0.7;
    }
  `;
  document.head.appendChild(style);
}

// ============================================================================
// Conversion helpers
// ============================================================================

function dagToPlanSteps(
  graph: DagGraph,
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>,
  failureReasons: Record<string, string>,
): PlanStep[] {
  return (graph.tasks ?? []).map((task, i) => {
    const taskStatus = dagTaskStatus[task.id] ?? 'pending';
    const planStatus: PlanStep['status'] =
      taskStatus === 'completed' ? 'done' :
      taskStatus === 'working' ? 'in_progress' :
      taskStatus === 'failed' ? 'error' :
      taskStatus === 'cancelled' ? 'cancelled' :
      'pending';
    return {
      index: i + 1,
      text: task.goal,
      status: planStatus,
      agent: task.role,
      taskId: task.id,
      dependsOn: task.depends_on,
      failureReason: failureReasons[task.id],
      isRemediation: task.is_remediation,
    };
  });
}

/**
 * Fallback: Parses orchestrator output for numbered steps and tracks their completion
 * based on agent_finished events. Used when no DAG graph is available.
 */
function extractPlan(activities: ActivityEntry[]): PlanStep[] {
  const steps: PlanStep[] = [];
  const finishedAgents = new Set<string>();
  const errorAgents = new Map<string, string>();
  const workingAgents = new Set<string>();

  for (const a of activities) {
    if (a.type === 'agent_finished' && a.agent) {
      if (a.is_error) {
        errorAgents.set(a.agent, a.failure_reason ?? 'Unknown error');
      } else {
        finishedAgents.add(a.agent);
      }
      workingAgents.delete(a.agent);
    }
    if (a.type === 'agent_started' && a.agent) {
      workingAgents.add(a.agent);
    }
  }

  for (const a of activities) {
    if ((a.type === 'agent_text' || a.type === 'agent_result') && a.agent?.toLowerCase() === 'orchestrator' && a.content) {
      const lines = a.content.split('\n');
      for (const line of lines) {
        const match = line.match(/^\s*(?:(\d+)[.)]\s+|[-*]\s+)(.+)/);
        if (match) {
          const text = match[2].trim();
          if (text.length < 10 || text.length > 200) continue;

          let agent: string | undefined;
          const lowerText = text.toLowerCase();
          const boldAgentMatch = text.match(/\*\*([a-z_]+)\*\*\s*:/);
          if (boldAgentMatch) {
            agent = boldAgentMatch[1];
          } else if (lowerText.includes('develop') || lowerText.includes('implement') || lowerText.includes('code') || lowerText.includes('write')) {
            agent = 'developer';
          } else if (lowerText.includes('review') || lowerText.includes('check')) {
            agent = 'reviewer';
          } else if (lowerText.includes('test')) {
            agent = 'tester';
          } else if (lowerText.includes('deploy') || lowerText.includes('docker') || lowerText.includes('ci/cd')) {
            agent = 'devops';
          } else if (lowerText.includes('research')) {
            agent = 'researcher';
          }

          let status: PlanStep['status'] = 'pending';
          let failureReason: string | undefined;
          if (agent) {
            if (errorAgents.has(agent)) {
              status = 'error';
              failureReason = errorAgents.get(agent);
            } else if (finishedAgents.has(agent)) {
              status = 'done';
            } else if (workingAgents.has(agent)) {
              status = 'in_progress';
            }
          }

          steps.push({ index: steps.length + 1, text, status, agent, failureReason });
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
        let failureReason: string | undefined;
        if (errorAgents.has(agent)) {
          status = 'error';
          failureReason = errorAgents.get(agent);
        } else if (finishedAgents.has(agent)) {
          status = 'done';
        } else if (workingAgents.has(agent)) {
          status = 'in_progress';
        }

        steps.push({ index: steps.length + 1, text: a.task, status, agent, failureReason });
      }
    }
  }

  return steps;
}

// ============================================================================
// Status Icons
// ============================================================================

function CheckmarkIcon({ color }: { color: string }): React.ReactElement {
  return (
    <div
      className="plan-check-icon w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
      style={{ background: `${color}18`, border: `2px solid ${color}` }}
      role="img"
      aria-label="Completed"
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path className="plan-check-path" d="M3 8l3.5 3.5L13 5" />
      </svg>
    </div>
  );
}

function SpinnerIcon({ color, glow }: { color: string; glow: string }): React.ReactElement {
  return (
    <div
      className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 relative"
      style={{ background: `${color}10`, boxShadow: `0 0 12px ${glow}` }}
      role="img"
      aria-label="In progress"
    >
      {/* Center dot */}
      <div className="w-2 h-2 rounded-full plan-pulse" style={{ background: color }} />
      {/* Spinning arc */}
      <svg
        className="plan-spinner-ring absolute inset-0"
        width="28"
        height="28"
        viewBox="0 0 28 28"
        fill="none"
      >
        <circle cx="14" cy="14" r="12" stroke={`${color}25`} strokeWidth="2" />
        <path
          d="M14 2 A12 12 0 0 1 26 14"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}

function ErrorIcon(): React.ReactElement {
  return (
    <div
      className="plan-error-shake w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
      style={{ background: 'var(--glow-red)', border: '2px solid var(--accent-red)' }}
      role="img"
      aria-label="Failed"
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="var(--accent-red)" strokeWidth="2.5" strokeLinecap="round">
        <path d="M4 4l8 8M12 4l-8 8" />
      </svg>
    </div>
  );
}

function CancelledIcon(): React.ReactElement {
  return (
    <div
      className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
      style={{ background: 'rgba(160,160,160,0.1)', border: '2px solid var(--text-muted)' }}
      role="img"
      aria-label="Cancelled"
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round">
        <path d="M4 8h8" />
      </svg>
    </div>
  );
}

function PendingIcon(): React.ReactElement {
  return (
    <div
      className="w-7 h-7 rounded-full flex-shrink-0"
      style={{ background: 'var(--bg-elevated)', border: '2px solid var(--border-subtle)' }}
      role="img"
      aria-label="Pending"
    />
  );
}

function StatusIcon({ status, agentName }: { status: PlanStep['status']; agentName?: string }): React.ReactElement {
  const accent = agentName ? getAgentAccent(agentName) : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };

  switch (status) {
    case 'done':
      return <CheckmarkIcon color="var(--accent-green)" />;
    case 'in_progress':
      return <SpinnerIcon color={accent.color} glow={accent.glow} />;
    case 'error':
      return <ErrorIcon />;
    case 'cancelled':
      return <CancelledIcon />;
    default:
      return <PendingIcon />;
  }
}

// ============================================================================
// Dependency Arrow (visual indicator between steps)
// ============================================================================

function DependencyBadges({
  dependsOn,
  steps,
}: {
  dependsOn: string[];
  steps: PlanStep[];
}): React.ReactElement | null {
  if (!dependsOn || dependsOn.length === 0) return null;

  const taskMap = new Map(steps.map(s => [s.taskId, s]));

  return (
    <div className="flex items-center gap-1 mt-1 flex-wrap">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
        <path d="M8 2v8M4 6l4 4 4-4" />
      </svg>
      {dependsOn.map(depId => {
        const dep = taskMap.get(depId);
        const depStatus = dep?.status ?? 'pending';
        const depColor =
          depStatus === 'done' ? 'var(--accent-green)' :
          depStatus === 'in_progress' ? 'var(--accent-blue)' :
          depStatus === 'error' ? 'var(--accent-red)' :
          'var(--text-muted)';

        return (
          <span
            key={depId}
            className="text-[9px] font-mono px-1.5 py-0.5 rounded"
            style={{
              background: `${depColor}12`,
              color: depColor,
              border: `1px solid ${depColor}25`,
            }}
            title={dep ? `Depends on: ${dep.text.slice(0, 60)}` : `Depends on: ${depId}`}
          >
            {depId}
          </span>
        );
      })}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export default function PlanView({
  activities,
  dagGraph,
  dagTaskStatus = {},
  dagTaskFailureReasons = {},
}: Props): React.ReactElement {
  const isDagMode = !!(dagGraph && dagGraph.tasks && dagGraph.tasks.length > 0);
  const prevStepsRef = useRef<Map<string, PlanStep['status']>>(new Map());

  // Inject CSS animations on mount
  useEffect(() => { ensureStyles(); }, []);

  const steps = useMemo(() => {
    if (dagGraph && dagGraph.tasks && dagGraph.tasks.length > 0) {
      return dagToPlanSteps(dagGraph, dagTaskStatus, dagTaskFailureReasons);
    }
    return extractPlan(activities);
  }, [activities, dagGraph, dagTaskStatus, dagTaskFailureReasons]);

  // Track which steps just transitioned for animation triggers
  const transitions = useMemo(() => {
    const result = new Map<string, { from: PlanStep['status']; to: PlanStep['status'] }>();
    for (const step of steps) {
      const key = step.taskId ?? `idx-${step.index}`;
      const prev = prevStepsRef.current.get(key);
      if (prev && prev !== step.status) {
        result.set(key, { from: prev, to: step.status });
      }
    }
    // Update ref for next render
    const nextMap = new Map<string, PlanStep['status']>();
    for (const step of steps) {
      nextMap.set(step.taskId ?? `idx-${step.index}`, step.status);
    }
    prevStepsRef.current = nextMap;
    return result;
  }, [steps]);

  const completedCount = steps.filter(s => s.status === 'done').length;
  const [collapseCompleted, setCollapseCompleted] = useState(false);
  const errorCount = steps.filter(s => s.status === 'error').length;
  const cancelledCount = steps.filter(s => s.status === 'cancelled').length;
  const inProgressCount = steps.filter(s => s.status === 'in_progress').length;
  const hasFailures = errorCount > 0 || cancelledCount > 0;
  const pct = steps.length > 0 ? Math.round((completedCount / steps.length) * 100) : 0;
  const activeStep = steps.find(s => s.status === 'in_progress');

  // ── Empty state ──
  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}
        >
          📋
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>
          No plan yet
        </p>
        <p className="text-xs mt-1 text-center" style={{ color: 'var(--text-muted)' }}>
          Send a task and the agent will show its execution plan here
        </p>
      </div>
    );
  }

  // Status summary for aria-live
  const statusSummary = `Plan: ${completedCount} of ${steps.length} tasks completed${
    inProgressCount > 0 ? `, ${inProgressCount} in progress` : ''
  }${errorCount > 0 ? `, ${errorCount} failed` : ''}`;

  return (
    <div className="p-4">
      {/* Accessible live region for screen readers */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {statusSummary}
      </div>

      {/* Vision banner (DAG mode) */}
      {isDagMode && dagGraph?.vision && (
        <div
          className="mb-4 px-3 py-2 rounded-lg text-xs leading-relaxed"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-secondary)',
          }}
        >
          <span className="font-semibold" style={{ color: 'var(--accent-blue)' }}>
            🎯 Vision:{' '}
          </span>
          {dagGraph.vision}
        </div>
      )}

      {/* Progress header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1.5">
            <span
              className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
            >
              {isDagMode ? 'DAG Execution Plan' : 'Execution Plan'}
            </span>
            <span
              className="text-xs font-bold"
              style={{
                color: hasFailures
                  ? 'var(--accent-red)'
                  : pct === 100
                    ? 'var(--accent-green)'
                    : 'var(--accent-blue)',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {pct}%
              {hasFailures
                ? ` (${errorCount > 0 ? `${errorCount} failed` : ''}${
                    errorCount > 0 && cancelledCount > 0 ? ', ' : ''
                  }${cancelledCount > 0 ? `${cancelledCount} cancelled` : ''})`
                : ''}
            </span>
          </div>
          {/* Progress bar */}
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
            <div
              className="h-full rounded-full transition-all duration-700 ease-out relative"
              style={{
                width: `${pct}%`,
                background: hasFailures
                  ? 'var(--accent-red)'
                  : pct === 100
                    ? 'var(--accent-green)'
                    : 'linear-gradient(90deg, var(--accent-blue), var(--accent-cyan))',
                boxShadow:
                  pct > 0
                    ? `0 0 8px ${
                        hasFailures
                          ? 'var(--glow-red)'
                          : pct === 100
                            ? 'var(--glow-green)'
                            : 'var(--glow-blue)'
                      }`
                    : 'none',
              }}
            >
              {pct > 0 && pct < 100 && (
                <div className="absolute inset-0 overflow-hidden rounded-full">
                  <div
                    className="absolute inset-0 plan-shimmer"
                    style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent)' }}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
        <div
          className="flex-shrink-0 text-xs px-2.5 py-1 rounded-lg"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {completedCount}/{steps.length}
        </div>
      </div>

      {/* Collapse toggle for completed tasks */}
      {completedCount >= 3 && steps.length > completedCount && (
        <button
          onClick={() => setCollapseCompleted(prev => !prev)}
          className="w-full flex items-center gap-2 mb-3 px-3 py-1.5 rounded-lg text-xs transition-colors hover:opacity-80"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
          }}
          aria-expanded={!collapseCompleted}
          aria-label={collapseCompleted ? `Show ${completedCount} completed tasks` : `Hide ${completedCount} completed tasks`}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            style={{
              transform: collapseCompleted ? 'rotate(-90deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s',
            }}
            aria-hidden="true"
          >
            <path d="M4 6l4 4 4-4" />
          </svg>
          {collapseCompleted
            ? `Show ${completedCount} completed tasks`
            : `Hide ${completedCount} completed tasks`}
        </button>
      )}

      {/* Timeline */}
      <div className="relative" role="list" aria-label="Execution plan tasks">
        {/* Vertical timeline line */}
        <div
          className="absolute left-[13px] top-4 bottom-4 w-[2px]"
          style={{ background: 'var(--border-subtle)' }}
          aria-hidden="true"
        />

        <div className="space-y-0">
          {steps
            .filter(step => !(collapseCompleted && step.status === 'done'))
            .map((step) => {
              const accent = step.agent
                ? getAgentAccent(step.agent)
                : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };
              const isActive = step.status === 'in_progress';
              const isDone = step.status === 'done';
              const isError = step.status === 'error';
              const isCancelled = step.status === 'cancelled';
              const stepKey = step.taskId ?? `idx-${step.index}`;
              const transition = transitions.get(stepKey);
              const justCompleted = transition?.to === 'done';
              const justFailed = transition?.to === 'error';
              const justStarted = transition?.to === 'in_progress';

              return (
                <div
                  key={stepKey}
                  className={`plan-step flex items-start gap-3 relative py-2.5 transition-all duration-300
                    ${justStarted ? 'plan-fade-in' : ''}
                    ${justFailed ? 'plan-error-shake' : ''}`}
                  style={{
                    opacity: isDone ? 0.6 : isCancelled ? 0.45 : 1,
                  }}
                  role="listitem"
                  aria-label={`Task ${step.index}: ${step.text} — ${
                    isDone ? 'completed' : isActive ? 'in progress' : isError ? 'failed' : isCancelled ? 'cancelled' : 'pending'
                  }`}
                >
                  {/* Status icon on the timeline */}
                  <div className="relative z-10">
                    <StatusIcon status={step.status} agentName={step.agent} />
                  </div>

                  {/* Content card */}
                  <div
                    className="flex-1 min-w-0 rounded-xl px-3.5 py-2.5 transition-all duration-300"
                    style={{
                      background: isActive
                        ? accent.bg
                        : isError
                          ? 'var(--glow-red)'
                          : isCancelled
                            ? 'rgba(160,160,160,0.05)'
                            : 'transparent',
                      border: isActive
                        ? `1px solid ${accent.color}30`
                        : isError
                          ? '1px solid rgba(245,71,91,0.15)'
                          : isCancelled
                            ? '1px solid rgba(160,160,160,0.1)'
                            : '1px solid transparent',
                      boxShadow: isActive ? `0 0 15px ${accent.glow}` : 'none',
                    }}
                  >
                    {/* Task text */}
                    <p
                      className={`text-sm leading-relaxed ${isDone || isCancelled ? 'line-through' : ''}`}
                      style={{
                        color: isDone
                          ? 'var(--text-muted)'
                          : isActive
                            ? 'var(--text-primary)'
                            : 'var(--text-secondary)',
                      }}
                    >
                      {step.isRemediation && (
                        <span className="mr-1" title="Remediation task">🔧</span>
                      )}
                      {step.text}
                    </p>

                    {/* Failure reason */}
                    {isError && step.failureReason && (
                      <div
                        className="mt-1.5 px-2.5 py-1.5 rounded-lg text-[11px] leading-snug"
                        style={{
                          background: 'rgba(245,71,91,0.08)',
                          border: '1px solid rgba(245,71,91,0.12)',
                          color: 'var(--accent-red)',
                        }}
                        role="alert"
                      >
                        <span className="font-semibold">Error:</span>{' '}
                        {step.failureReason.length > 150
                          ? `${step.failureReason.slice(0, 150)}…`
                          : step.failureReason}
                      </div>
                    )}

                    {/* Dependency badges (DAG mode) */}
                    {step.dependsOn && step.dependsOn.length > 0 && (
                      <DependencyBadges dependsOn={step.dependsOn} steps={steps} />
                    )}

                    {/* Agent badge + status label */}
                    {step.agent && (
                      <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                        <span className="text-xs" aria-hidden="true">
                          {AGENT_ICONS[step.agent] || '🔧'}
                        </span>
                        <span
                          className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                          style={{
                            background: accent.bg,
                            color: accent.color,
                            border: `1px solid ${accent.color}20`,
                          }}
                        >
                          {AGENT_LABELS[step.agent] || step.agent}
                        </span>
                        {step.taskId && (
                          <span
                            className="text-[9px] font-mono opacity-40"
                            style={{ color: 'var(--text-muted)' }}
                          >
                            {step.taskId}
                          </span>
                        )}
                        {isActive && (
                          <span
                            className="text-[9px] font-bold tracking-wider plan-pulse"
                            style={{ color: accent.color, fontFamily: 'var(--font-mono)' }}
                          >
                            WORKING
                          </span>
                        )}
                        {isDone && justCompleted && (
                          <span
                            className="text-[9px] font-bold tracking-wider plan-fade-in"
                            style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}
                          >
                            ✓ DONE
                          </span>
                        )}
                        {isDone && !justCompleted && (
                          <span
                            className="text-[9px] font-bold tracking-wider"
                            style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}
                          >
                            DONE
                          </span>
                        )}
                        {isError && (
                          <span
                            className="text-[9px] font-bold tracking-wider"
                            style={{ color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}
                          >
                            FAILED
                          </span>
                        )}
                        {isCancelled && (
                          <span
                            className="text-[9px] font-bold tracking-wider"
                            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                          >
                            CANCELLED
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

      {/* Active step highlight */}
      {activeStep && activeStep.agent && (
        <div
          className="mt-4 pt-3 flex items-center gap-2"
          style={{ borderTop: '1px solid var(--border-dim)' }}
        >
          <div
            className="w-2 h-2 rounded-full plan-pulse"
            style={{ background: getAgentAccent(activeStep.agent).color }}
          />
          <span
            className="text-xs"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
          >
            {AGENT_LABELS[activeStep.agent] || activeStep.agent} is working on step{' '}
            {activeStep.index}
          </span>
        </div>
      )}
    </div>
  );
}
