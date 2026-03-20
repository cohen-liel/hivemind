import { useMemo, useState, useRef, useEffect } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';
import { useFeedback } from '../hooks/useFeedback';
import { dagToPlanSteps, extractPlan } from './planViewHelpers';
import type { PlanStep, DagGraph, StatusTransition } from './planViewHelpers';
import './PlanView.css';
import '../styles/animations.css';

// ============================================================================
// Props
// ============================================================================

interface Props {
  activities: ActivityEntry[];
  dagGraph?: DagGraph | null;
  dagTaskStatus?: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled'>;
  dagTaskFailureReasons?: Record<string, string>;
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
      <div className="w-2 h-2 rounded-full plan-pulse" style={{ background: color }} />
      <svg className="plan-spinner-ring absolute inset-0" width="28" height="28" viewBox="0 0 28 28" fill="none">
        <circle cx="14" cy="14" r="12" stroke={`${color}25`} strokeWidth="2" />
        <path d="M14 2 A12 12 0 0 1 26 14" stroke={color} strokeWidth="2" strokeLinecap="round" />
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
    case 'done': return <CheckmarkIcon color="var(--accent-green)" />;
    case 'in_progress': return <SpinnerIcon color={accent.color} glow={accent.glow} />;
    case 'error': return <ErrorIcon />;
    case 'cancelled': return <CancelledIcon />;
    default: return <PendingIcon />;
  }
}

// ============================================================================
// Celebration Overlay
// ============================================================================

const PARTICLE_COLORS = ['var(--accent-green)', 'var(--accent-cyan)', 'var(--accent-blue)', 'var(--accent-purple)', '#FFD700'];

function CelebrationOverlay(): React.ReactElement {
  const particles = useMemo(() =>
    Array.from({ length: 12 }, (_, i) => {
      const angle = (i / 12) * Math.PI * 2;
      const radius = 30 + Math.random() * 40;
      return {
        id: i,
        color: PARTICLE_COLORS[i % PARTICLE_COLORS.length],
        px: `${Math.cos(angle) * radius}px`,
        py: `${Math.sin(angle) * radius}px`,
        delay: `${i * 0.05}s`,
        left: `${45 + Math.random() * 10}%`,
        top: `${40 + Math.random() * 20}%`,
        size: 4 + Math.random() * 4,
      };
    }), []);

  return (
    <div className="planview-celebration plan-celebrate-burst" role="status" aria-label="All tasks completed successfully">
      <div className="planview-celebration-ring plan-celebrate-ring" />
      <div className="planview-celebration-particles">
        {particles.map(p => (
          <div
            key={p.id}
            className="planview-particle"
            style={{ '--px': p.px, '--py': p.py, left: p.left, top: p.top, width: p.size, height: p.size, background: p.color, animationDelay: p.delay } as React.CSSProperties}
          />
        ))}
      </div>
      <div className="relative z-10">
        <div className="text-xl mb-1">🎉</div>
        <div className="text-sm font-bold" style={{ color: 'var(--accent-green)' }}>All tasks completed</div>
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>Execution plan finished successfully</div>
      </div>
    </div>
  );
}

// ============================================================================
// Progress Ticker
// ============================================================================

function ProgressTicker({ completed, total, inProgress, failed, isAllDone, justChanged }: {
  completed: number; total: number; inProgress: number; failed: number; isAllDone: boolean; justChanged: boolean;
}): React.ReactElement {
  return (
    <div
      className={`planview-ticker ${isAllDone ? 'planview-ticker--complete' : ''} ${justChanged ? 'plan-ticker-pulse' : ''}`}
      aria-label={`${completed} of ${total} tasks completed`}
    >
      <span
        className={`planview-ticker-count ${justChanged ? 'plan-count-up' : ''}`}
        style={{ color: isAllDone ? 'var(--accent-green)' : failed > 0 ? 'var(--accent-red)' : 'var(--text-secondary)' }}
      >
        {completed}
      </span>
      <span style={{ color: 'var(--text-muted)' }}>/</span>
      <span style={{ color: 'var(--text-muted)' }}>{total}</span>
      {inProgress > 0 && (
        <span className="ml-1 flex items-center gap-1" style={{ color: 'var(--accent-blue)', fontSize: 10 }}>
          <span className="w-1.5 h-1.5 rounded-full plan-pulse" style={{ background: 'var(--accent-blue)' }} />
          {inProgress} active
        </span>
      )}
      {failed > 0 && <span className="ml-1" style={{ color: 'var(--accent-red)', fontSize: 10 }}>{failed} failed</span>}
      {isAllDone && <span className="plan-complete-badge ml-1" style={{ fontSize: 10 }}>✓</span>}
    </div>
  );
}

// ============================================================================
// Dependency Badges
// ============================================================================

function DependencyBadges({ dependsOn, steps }: { dependsOn: string[]; steps: PlanStep[] }): React.ReactElement | null {
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
        const depColor = depStatus === 'done' ? 'var(--accent-green)' : depStatus === 'in_progress' ? 'var(--accent-blue)' : depStatus === 'error' ? 'var(--accent-red)' : 'var(--text-muted)';
        return (
          <span key={depId} className="text-[9px] font-mono px-1.5 py-0.5 rounded"
            style={{ background: `${depColor}12`, color: depColor, border: `1px solid ${depColor}25` }}
            title={dep ? `Depends on: ${dep.text.slice(0, 60)}` : `Depends on: ${depId}`}
          >{depId}</span>
        );
      })}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export default function PlanView({ activities, dagGraph, dagTaskStatus = {}, dagTaskFailureReasons = {} }: Props): React.ReactElement {
  const isDagMode = !!(dagGraph?.tasks?.length);
  const prevStepsRef = useRef<Map<string, PlanStep['status']>>(new Map());
  const prevCompletedRef = useRef<number>(0);
  const [showCelebration, setShowCelebration] = useState(false);
  const celebrationShownRef = useRef(false);
  const [collapseCompleted, setCollapseCompleted] = useState(false);
  const feedback = useFeedback({ enabled: true });

  const steps = useMemo(() => {
    if (dagGraph?.tasks?.length) return dagToPlanSteps(dagGraph, dagTaskStatus, dagTaskFailureReasons);
    return extractPlan(activities);
  }, [activities, dagGraph, dagTaskStatus, dagTaskFailureReasons]);

  const transitions = useMemo(() => {
    const result = new Map<string, StatusTransition>();
    for (const step of steps) {
      const key = step.taskId ?? `idx-${step.index}`;
      const prev = prevStepsRef.current.get(key);
      if (prev && prev !== step.status) result.set(key, { from: prev, to: step.status });
    }
    const nextMap = new Map<string, PlanStep['status']>();
    for (const step of steps) nextMap.set(step.taskId ?? `idx-${step.index}`, step.status);
    prevStepsRef.current = nextMap;
    return result;
  }, [steps]);

  const completedCount = steps.filter(s => s.status === 'done').length;
  const errorCount = steps.filter(s => s.status === 'error').length;
  const cancelledCount = steps.filter(s => s.status === 'cancelled').length;
  const inProgressCount = steps.filter(s => s.status === 'in_progress').length;
  const hasFailures = errorCount > 0 || cancelledCount > 0;
  const pct = steps.length > 0 ? Math.round((completedCount / steps.length) * 100) : 0;
  const isAllDone = steps.length > 0 && completedCount === steps.length;
  const activeStep = steps.find(s => s.status === 'in_progress');
  const justCountChanged = completedCount !== prevCompletedRef.current && prevCompletedRef.current > 0;

  useEffect(() => {
    for (const [, t] of transitions) {
      if (t.to === 'done') feedback.onTaskComplete();
      else if (t.to === 'error') feedback.onTaskFailed();
      else if (t.to === 'in_progress') feedback.onTaskStarted();
    }
  }, [transitions, feedback]);

  useEffect(() => {
    if (isAllDone && !celebrationShownRef.current && steps.length > 0) {
      celebrationShownRef.current = true;
      setShowCelebration(true);
      feedback.onAllComplete();
      const timer = setTimeout(() => setShowCelebration(false), 4000);
      return () => clearTimeout(timer);
    }
    if (!isAllDone) celebrationShownRef.current = false;
  }, [isAllDone, steps.length, feedback]);

  useEffect(() => { prevCompletedRef.current = completedCount; }, [completedCount]);

  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>📋</div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No plan yet</p>
        <p className="text-xs mt-1 text-center" style={{ color: 'var(--text-muted)' }}>
          Send a task and the agent will show its execution plan here
        </p>
      </div>
    );
  }

  const statusSummary = `Plan: ${completedCount} of ${steps.length} tasks completed${
    inProgressCount > 0 ? `, ${inProgressCount} in progress` : ''}${errorCount > 0 ? `, ${errorCount} failed` : ''}`;

  return (
    <div className="p-4">
      <div className="sr-only" aria-live="polite" aria-atomic="true">{statusSummary}</div>

      {showCelebration && <CelebrationOverlay />}

      {isDagMode && dagGraph?.vision && (
        <div className="glass-panel glow-border-blue mb-4 px-4 py-3 rounded-xl text-xs leading-relaxed"
          style={{ color: 'var(--text-secondary)' }}>
          <div className="flex items-start gap-2">
            <span className="text-base flex-shrink-0" aria-hidden="true">🎯</span>
            <div>
              <span className="text-[10px] font-bold uppercase tracking-wider block mb-1"
                style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>Vision</span>
              <span className="text-sm leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                {dagGraph.vision}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Progress header with ticker */}
      <div className="flex items-center gap-3 mb-5">
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {isDagMode ? 'DAG Execution Plan' : 'Execution Plan'}
            </span>
            <span className="text-xs font-bold" style={{
              color: hasFailures ? 'var(--accent-red)' : pct === 100 ? 'var(--accent-green)' : 'var(--accent-blue)',
              fontFamily: 'var(--font-mono)',
            }}>
              {pct}%{hasFailures ? ` (${errorCount > 0 ? `${errorCount} failed` : ''}${
                errorCount > 0 && cancelledCount > 0 ? ', ' : ''}${cancelledCount > 0 ? `${cancelledCount} cancelled` : ''})` : ''}
            </span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
            <div className={`h-full rounded-full transition-all duration-700 ease-out relative ${isAllDone ? 'plan-bar-complete' : ''}`}
              style={{
                width: `${pct}%`,
                background: hasFailures ? 'var(--accent-red)' : pct === 100 ? 'var(--accent-green)' : 'linear-gradient(90deg, var(--accent-blue), var(--accent-cyan))',
                boxShadow: pct > 0 ? `0 0 8px ${hasFailures ? 'var(--glow-red)' : pct === 100 ? 'var(--glow-green)' : 'var(--glow-blue)'}` : 'none',
              }}>
              {pct > 0 && pct < 100 && (
                <div className="absolute inset-0 overflow-hidden rounded-full">
                  <div className="absolute inset-0 plan-shimmer" style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent)' }} />
                </div>
              )}
            </div>
          </div>
        </div>
        <ProgressTicker completed={completedCount} total={steps.length} inProgress={inProgressCount}
          failed={errorCount} isAllDone={isAllDone} justChanged={justCountChanged} />
      </div>

      {completedCount >= 3 && steps.length > completedCount && (
        <button onClick={() => setCollapseCompleted(prev => !prev)} className="planview-collapse-btn"
          aria-expanded={!collapseCompleted}
          aria-label={collapseCompleted ? `Show ${completedCount} completed tasks` : `Hide ${completedCount} completed tasks`}>
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            className={`planview-collapse-chevron ${collapseCompleted ? 'planview-collapse-chevron--collapsed' : ''}`} aria-hidden="true">
            <path d="M4 6l4 4 4-4" />
          </svg>
          {collapseCompleted ? `Show ${completedCount} completed tasks` : `Hide ${completedCount} completed tasks`}
        </button>
      )}

      {/* Timeline */}
      <div className="relative" role="list" aria-label="Execution plan tasks">
        <div className="planview-timeline-line" aria-hidden="true" />
        <div className="space-y-0">
          {steps.filter(step => !(collapseCompleted && step.status === 'done')).map((step) => {
            const accent = step.agent ? getAgentAccent(step.agent) : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };
            const isActive = step.status === 'in_progress';
            const isDone = step.status === 'done';
            const isError = step.status === 'error';
            const isCancelled = step.status === 'cancelled';
            const stepKey = step.taskId ?? `idx-${step.index}`;
            const transition = transitions.get(stepKey);
            const justCompleted = transition?.to === 'done';
            const justFailed = transition?.to === 'error';
            const justStarted = transition?.to === 'in_progress';
            const stepStatusClass = isActive ? 'planview-step--active' : isDone ? 'planview-step--done' : isError ? 'planview-step--error' : isCancelled ? 'planview-step--cancelled' : 'planview-step--pending';

            return (
              <div key={stepKey}
                className={`planview-step ${stepStatusClass} ${justStarted ? 'plan-fade-in' : ''} ${justFailed ? 'plan-error-shake' : ''} ${justCompleted ? 'plan-status-slide-in agent-done-flash' : ''}`}
                role="listitem"
                aria-label={`Task ${step.index}: ${step.text} — ${isDone ? 'completed' : isActive ? 'in progress' : isError ? 'failed' : isCancelled ? 'cancelled' : 'pending'}`}>
                <div className="planview-icon-slot">
                  <StatusIcon status={step.status} agentName={step.agent} />
                </div>
                <div className={`planview-card ${isActive ? 'planview-card--active' : ''}`}
                  style={{
                    '--step-glow': accent.glow,
                    background: isActive ? accent.bg : isError ? 'var(--glow-red)' : isCancelled ? 'rgba(160,160,160,0.05)' : 'transparent',
                    borderColor: isActive ? `${accent.color}30` : isError ? 'rgba(245,71,91,0.15)' : isCancelled ? 'rgba(160,160,160,0.1)' : 'transparent',
                  } as React.CSSProperties}>
                  <p className={`text-sm leading-relaxed ${isDone || isCancelled ? 'line-through' : ''}`}
                    style={{ color: isDone ? 'var(--text-muted)' : isActive ? 'var(--text-primary)' : 'var(--text-secondary)', transition: 'color 0.3s ease' }}>
                    {step.isRemediation && <span className="mr-1" title="Remediation task">🔧</span>}
                    {step.text}
                  </p>
                  {isError && step.failureReason && (
                    <div className="planview-failure" role="alert">
                      <span className="font-semibold">Error:</span>{' '}
                      {step.failureReason.length > 150 ? `${step.failureReason.slice(0, 150)}…` : step.failureReason}
                    </div>
                  )}
                  {step.dependsOn && step.dependsOn.length > 0 && <DependencyBadges dependsOn={step.dependsOn} steps={steps} />}
                  {step.agent && (
                    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                      <span className="text-xs" aria-hidden="true">{AGENT_ICONS[step.agent] || '🔧'}</span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: accent.bg, color: accent.color, border: `1px solid ${accent.color}20` }}>
                        {AGENT_LABELS[step.agent] || step.agent}
                      </span>
                      {step.taskId && <span className="text-[9px] font-mono opacity-40" style={{ color: 'var(--text-muted)' }}>{step.taskId}</span>}
                      {isActive && <span className="text-[9px] font-bold tracking-wider plan-pulse plan-status-slide-in" style={{ color: accent.color, fontFamily: 'var(--font-mono)' }}>WORKING</span>}
                      {isDone && justCompleted && <span className="text-[9px] font-bold tracking-wider plan-complete-badge" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>✓ DONE</span>}
                      {isDone && !justCompleted && <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>DONE</span>}
                      {isError && <span className="text-[9px] font-bold tracking-wider plan-status-slide-in" style={{ color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>FAILED</span>}
                      {isCancelled && <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>CANCELLED</span>}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {activeStep?.agent && (
        <div className="mt-4 pt-3 flex items-center gap-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
          <div className="w-2 h-2 rounded-full plan-pulse" style={{ background: getAgentAccent(activeStep.agent).color }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {AGENT_LABELS[activeStep.agent] || activeStep.agent} is working on step {activeStep.index}
          </span>
        </div>
      )}
    </div>
  );
}
