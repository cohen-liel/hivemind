import { useMemo, useState, useRef, useEffect, useCallback } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';
import { useFeedback } from '../hooks/useFeedback';
import { dagToPlanSteps, extractPlan, groupStepsByRound, computeProgress, buildArtifactEdges, buildUpstreamContexts, estimateTaskEtas, detectRemediationChains } from './planViewHelpers';
import type { PlanStep, DagGraph, StatusTransition, ArtifactEdge, UpstreamContext, TaskEta, RemediationChain } from './planViewHelpers';
import './PlanView.css';
import '../styles/animations.css';

// ============================================================================
// Constants
// ============================================================================

const AGENT_ROLES = [
  { value: 'frontend_developer', label: 'Frontend Developer' },
  { value: 'backend_developer', label: 'Backend Developer' },
  { value: 'database_expert', label: 'Database Expert' },
  { value: 'devops', label: 'DevOps' },
  { value: 'test_engineer', label: 'Test Engineer' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'researcher', label: 'Researcher' },
  { value: 'security_auditor', label: 'Security Auditor' },
  { value: 'developer', label: 'Developer' },
  { value: 'pm', label: 'PM' },
] as const;

// ============================================================================
// Props
// ============================================================================

interface Props {
  activities: ActivityEntry[];
  dagGraph?: DagGraph | null;
  dagTaskStatus?: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled' | 'skipped'>;
  dagTaskFailureReasons?: Record<string, string>;
  projectId?: string;
  /** When true, the plan is still loading from the server. */
  isLoading?: boolean;
  /** Per-task start timestamps (unix seconds) for ETA calculation. */
  taskStartTimes?: Record<string, number>;
}

// ============================================================================
// API Helpers
// ============================================================================

function getApiKey(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="hivemind-auth-token"]');
  if (meta?.content) return meta.content;
  try {
    return localStorage.getItem('hivemind-auth-token') || '';
  } catch {
    return '';
  }
}

async function planApi<T>(
  method: 'PATCH' | 'POST' | 'DELETE',
  path: string,
  body?: Record<string, unknown>,
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(apiKey ? { 'X-API-Key': apiKey } : {}),
  };
  try {
    const res = await fetch(`/api${path}`, {
      method,
      headers,
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: json.error || json.detail || `HTTP ${res.status}` };
    }
    return { ok: true, data: json as T };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Network error' };
  }
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

function SkippedIcon(): React.ReactElement {
  return (
    <div
      className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
      style={{ background: 'rgba(160,160,160,0.08)', border: '2px solid rgba(160,160,160,0.4)' }}
      role="img"
      aria-label="Skipped"
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="rgba(160,160,160,0.6)" strokeWidth="2" strokeLinecap="round">
        <path d="M5 3l6 5-6 5" />
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
    case 'skipped': return <SkippedIcon />;
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

function ProgressTicker({ completed, total, inProgress, failed, skipped, isAllDone, justChanged }: {
  completed: number; total: number; inProgress: number; failed: number; skipped: number; isAllDone: boolean; justChanged: boolean;
}): React.ReactElement {
  const actionable = total - skipped;
  return (
    <div
      className={`planview-ticker ${isAllDone ? 'planview-ticker--complete' : ''} ${justChanged ? 'plan-ticker-pulse' : ''}`}
      aria-label={`${completed} of ${actionable} tasks completed${skipped > 0 ? `, ${skipped} skipped` : ''}`}
    >
      <span
        className={`planview-ticker-count ${justChanged ? 'plan-count-up' : ''}`}
        style={{ color: isAllDone ? 'var(--accent-green)' : failed > 0 ? 'var(--accent-red)' : 'var(--text-secondary)' }}
      >
        {completed}
      </span>
      <span style={{ color: 'var(--text-muted)' }}>/</span>
      <span style={{ color: 'var(--text-muted)' }}>{actionable}</span>
      {inProgress > 0 && (
        <span className="ml-1 flex items-center gap-1" style={{ color: 'var(--accent-blue)', fontSize: 10 }}>
          <span className="w-1.5 h-1.5 rounded-full plan-pulse" style={{ background: 'var(--accent-blue)' }} />
          {inProgress} active
        </span>
      )}
      {failed > 0 && <span className="ml-1" style={{ color: 'var(--accent-red)', fontSize: 10 }}>{failed} failed</span>}
      {skipped > 0 && <span className="ml-1" style={{ color: 'var(--text-muted)', fontSize: 10 }}>{skipped} skipped</span>}
      {isAllDone && <span className="plan-complete-badge ml-1" style={{ fontSize: 10 }}>✓</span>}
    </div>
  );
}

// ============================================================================
// Round Divider
// ============================================================================

function RoundDivider({ label }: { label: string }): React.ReactElement {
  return (
    <div className="planview-round-divider" aria-label={label}>
      <div className="planview-round-divider-line" />
      <span className="planview-round-divider-label">{label}</span>
      <div className="planview-round-divider-line" />
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
// Artifact Flow Indicator (shows data passing status between tasks)
// ============================================================================

function ArtifactFlowIndicator({ edges, taskId }: { edges: ArtifactEdge[]; taskId: string }): React.ReactElement | null {
  const inbound = edges.filter(e => e.toTaskId === taskId);
  if (inbound.length === 0) return null;

  const statusConfig: Record<ArtifactEdge['status'], { color: string; icon: string; label: string }> = {
    received: { color: 'var(--accent-green)', icon: '✓', label: 'Received' },
    partial: { color: 'var(--accent-amber)', icon: '◐', label: 'In progress' },
    missing: { color: 'var(--text-muted)', icon: '○', label: 'Waiting' },
  };

  return (
    <div className="flex items-center gap-1.5 mt-1 flex-wrap" aria-label="Artifact flow status">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
        <path d="M2 8h10M9 5l3 3-3 3" />
      </svg>
      {inbound.map(edge => {
        const cfg = statusConfig[edge.status];
        return (
          <span key={edge.fromTaskId}
            className="planview-artifact-badge"
            style={{ '--artifact-color': cfg.color } as React.CSSProperties}
            title={`Artifacts from ${edge.fromTaskId}: ${cfg.label}`}
            aria-label={`Artifacts from ${edge.fromTaskId}: ${cfg.label}`}
          >
            <span className="text-[8px]">{cfg.icon}</span>
            <span>{edge.fromTaskId}</span>
          </span>
        );
      })}
    </div>
  );
}

// ============================================================================
// Upstream Context Summary
// ============================================================================

function UpstreamContextSummary({ context }: { context: UpstreamContext }): React.ReactElement | null {
  const [expanded, setExpanded] = useState(false);
  if (context.upstreamTasks.length === 0) return null;

  const allDone = context.upstreamTasks.every(t => t.status === 'done');
  const hasErrors = context.upstreamTasks.some(t => t.status === 'error');

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setExpanded(prev => !prev)}
        className="planview-upstream-toggle"
        aria-expanded={expanded}
        aria-label={`${context.upstreamTasks.length} upstream tasks — click to ${expanded ? 'collapse' : 'expand'}`}
      >
        <svg width="8" height="8" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"
          style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>
          <path d="M6 4l4 4-4 4" />
        </svg>
        <span className="text-[9px]" style={{
          color: hasErrors ? 'var(--accent-red)' : allDone ? 'var(--accent-green)' : 'var(--text-muted)',
        }}>
          {context.upstreamTasks.length} upstream {context.upstreamTasks.length === 1 ? 'task' : 'tasks'}
          {allDone ? ' ✓' : hasErrors ? ' ✗' : ''}
        </span>
      </button>
      {expanded && (
        <div className="planview-upstream-list">
          {context.upstreamTasks.map(ut => {
            const statusColor = ut.status === 'done' ? 'var(--accent-green)'
              : ut.status === 'error' ? 'var(--accent-red)'
              : ut.status === 'in_progress' ? 'var(--accent-blue)'
              : 'var(--text-muted)';
            return (
              <div key={ut.taskId} className="planview-upstream-item">
                <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: statusColor }} />
                <span className="text-[9px] font-mono" style={{ color: statusColor }}>{ut.taskId}</span>
                <span className="text-[9px] truncate" style={{ color: 'var(--text-muted)' }}>
                  {ut.goalSummary}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// ETA Badge
// ============================================================================

function EtaBadge({ eta }: { eta: TaskEta }): React.ReactElement | null {
  if (eta.etaSeconds === null) return null;
  return (
    <span className="planview-eta-badge" title={`Estimated: ${eta.etaDisplay}`} aria-label={`Estimated time: ${eta.etaDisplay}`}>
      ⏱ {eta.etaDisplay}
    </span>
  );
}

// ============================================================================
// Remediation Badge
// ============================================================================

function RemediationBadge({ chain }: { chain: RemediationChain }): React.ReactElement {
  return (
    <span className={`planview-remediation-badge ${chain.isHealed ? 'planview-remediation-badge--healed' : ''}`}
      title={chain.isHealed ? 'Self-healed after failure' : `Remediation in progress (${chain.remediationTaskIds.length} retry${chain.remediationTaskIds.length > 1 ? 'ies' : ''})`}
      aria-label={chain.isHealed ? 'Self-healed' : 'Remediation in progress'}
    >
      {chain.isHealed ? '🩹 Healed' : `🔧 Retry #${chain.remediationTaskIds.length}`}
    </span>
  );
}

// ============================================================================
// Inline Edit Form (for pending tasks)
// ============================================================================

function InlineEditForm({ taskId, currentGoal, projectId, onClose }: {
  taskId: string;
  currentGoal: string;
  projectId: string;
  onClose: () => void;
}): React.ReactElement {
  const [goal, setGoal] = useState(currentGoal);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
    textareaRef.current?.select();
  }, []);

  const handleSave = useCallback(async (): Promise<void> => {
    const trimmed = goal.trim();
    if (trimmed.length < 10) {
      setError('Goal must be at least 10 characters');
      return;
    }
    if (trimmed === currentGoal) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    const result = await planApi('PATCH', `/projects/${projectId}/plan/tasks/${taskId}`, { goal: trimmed });
    setSaving(false);
    if (result.ok) {
      onClose();
    } else {
      setError(result.error);
    }
  }, [goal, currentGoal, projectId, taskId, onClose]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      onClose();
    }
  }, [handleSave, onClose]);

  return (
    <div className="mt-2 space-y-2">
      <textarea
        ref={textareaRef}
        value={goal}
        onChange={e => setGoal(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={3}
        className="planview-edit-textarea"
        aria-label="Edit task goal"
        disabled={saving}
      />
      {error && (
        <div className="text-[10px] px-2 py-1 rounded" style={{ color: 'var(--accent-red)', background: 'rgba(245,71,91,0.08)' }}>
          {error}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || goal.trim().length < 10}
          className="planview-action-btn planview-action-btn--save"
          aria-label="Save changes"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onClose}
          disabled={saving}
          className="planview-action-btn planview-action-btn--cancel"
          aria-label="Cancel editing"
        >
          Cancel
        </button>
        <span className="text-[9px] ml-auto" style={{ color: 'var(--text-muted)' }}>
          ⌘+Enter to save · Esc to cancel
        </span>
      </div>
    </div>
  );
}

// ============================================================================
// Delete Confirmation
// ============================================================================

function DeleteConfirmation({ taskId, projectId, onClose }: {
  taskId: string;
  projectId: string;
  onClose: () => void;
}): React.ReactElement {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = useCallback(async (): Promise<void> => {
    setDeleting(true);
    setError(null);
    const result = await planApi('DELETE', `/projects/${projectId}/plan/tasks/${taskId}`);
    setDeleting(false);
    if (result.ok) {
      onClose();
    } else {
      setError(result.error);
    }
  }, [projectId, taskId, onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div className="mt-2 px-3 py-2 rounded-lg" style={{ background: 'rgba(245,71,91,0.06)', border: '1px solid rgba(245,71,91,0.15)' }}>
      <p className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
        Remove this task from the plan? This cannot be undone.
      </p>
      {error && (
        <div className="text-[10px] px-2 py-1 rounded mb-2" style={{ color: 'var(--accent-red)', background: 'rgba(245,71,91,0.08)' }}>
          {error}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="planview-action-btn planview-action-btn--delete"
          aria-label="Confirm delete task"
        >
          {deleting ? 'Removing…' : 'Remove'}
        </button>
        <button
          onClick={onClose}
          disabled={deleting}
          className="planview-action-btn planview-action-btn--cancel"
          aria-label="Cancel delete"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Add Task Form
// ============================================================================

function AddTaskForm({ projectId, existingTaskIds, onClose }: {
  projectId: string;
  existingTaskIds: string[];
  onClose: () => void;
}): React.ReactElement {
  const nextNum = useMemo(() => {
    let max = 0;
    for (const id of existingTaskIds) {
      const match = id.match(/_(\d+)$/);
      if (match) max = Math.max(max, parseInt(match[1], 10));
    }
    return max + 1;
  }, [existingTaskIds]);

  const [taskId, setTaskId] = useState(`task_${String(nextNum).padStart(3, '0')}`);
  const [role, setRole] = useState<string>(AGENT_ROLES[0].value);
  const [goal, setGoal] = useState('');
  const [dependsOn, setDependsOn] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const goalRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    goalRef.current?.focus();
  }, []);

  const toggleDep = useCallback((depId: string): void => {
    setDependsOn(prev =>
      prev.includes(depId) ? prev.filter(d => d !== depId) : [...prev, depId],
    );
  }, []);

  const handleSubmit = useCallback(async (): Promise<void> => {
    const trimmedGoal = goal.trim();
    const trimmedId = taskId.trim();
    if (!trimmedId || !/^[a-zA-Z0-9_-]{1,64}$/.test(trimmedId)) {
      setError('Task ID must be 1-64 alphanumeric characters, hyphens, or underscores');
      return;
    }
    if (existingTaskIds.includes(trimmedId)) {
      setError(`Task ID "${trimmedId}" already exists`);
      return;
    }
    if (trimmedGoal.length < 10) {
      setError('Goal must be at least 10 characters');
      return;
    }
    setSaving(true);
    setError(null);
    const result = await planApi('POST', `/projects/${projectId}/plan/tasks`, {
      id: trimmedId,
      role,
      goal: trimmedGoal,
      constraints: [],
      depends_on: dependsOn,
      files_scope: [],
      acceptance_criteria: [],
    });
    setSaving(false);
    if (result.ok) {
      onClose();
    } else {
      setError(result.error);
    }
  }, [taskId, role, goal, dependsOn, projectId, existingTaskIds, onClose]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      onClose();
    }
  }, [handleSubmit, onClose]);

  return (
    <div
      className="glass-panel rounded-xl p-4 mb-4 space-y-3"
      style={{ border: '1px solid var(--accent-blue)', boxShadow: '0 0 20px rgba(99,140,255,0.08)' }}
      onKeyDown={handleKeyDown}
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-wider"
          style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
          Add New Task
        </span>
        <button onClick={onClose} className="planview-icon-btn" aria-label="Close add task form">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </div>

      <div className="flex gap-2">
        <div className="flex-1">
          <label className="planview-form-label" htmlFor="add-task-id">Task ID</label>
          <input
            id="add-task-id"
            type="text"
            value={taskId}
            onChange={e => setTaskId(e.target.value)}
            className="planview-form-input"
            placeholder="task_007"
            disabled={saving}
          />
        </div>
        <div className="flex-1">
          <label className="planview-form-label" htmlFor="add-task-role">Agent Role</label>
          <select
            id="add-task-role"
            value={role}
            onChange={e => setRole(e.target.value)}
            className="planview-form-input"
            disabled={saving}
          >
            {AGENT_ROLES.map(r => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="planview-form-label" htmlFor="add-task-goal">Goal</label>
        <textarea
          id="add-task-goal"
          ref={goalRef}
          value={goal}
          onChange={e => setGoal(e.target.value)}
          rows={3}
          className="planview-edit-textarea"
          placeholder="Describe what this task should accomplish (min 10 chars)…"
          disabled={saving}
        />
      </div>

      {existingTaskIds.length > 0 && (
        <div>
          <label className="planview-form-label">Dependencies (optional)</label>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {existingTaskIds.map(id => (
              <button
                key={id}
                type="button"
                onClick={() => toggleDep(id)}
                className={`planview-dep-chip ${dependsOn.includes(id) ? 'planview-dep-chip--selected' : ''}`}
                aria-pressed={dependsOn.includes(id)}
                aria-label={`Depend on ${id}`}
                disabled={saving}
              >
                {id}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="text-[10px] px-2 py-1 rounded" style={{ color: 'var(--accent-red)', background: 'rgba(245,71,91,0.08)' }}>
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSubmit}
          disabled={saving || goal.trim().length < 10}
          className="planview-action-btn planview-action-btn--save"
          aria-label="Create task"
        >
          {saving ? 'Creating…' : 'Create Task'}
        </button>
        <button
          onClick={onClose}
          disabled={saving}
          className="planview-action-btn planview-action-btn--cancel"
          aria-label="Cancel"
        >
          Cancel
        </button>
        <span className="text-[9px] ml-auto" style={{ color: 'var(--text-muted)' }}>
          ⌘+Enter to create
        </span>
      </div>
    </div>
  );
}

// ============================================================================
// Step Row (with edit/delete actions for pending tasks)
// ============================================================================

function StepRow({ step, steps, transition, projectId, editingTaskId, deletingTaskId, onEdit, onDelete, onCancelAction, artifactEdges, upstreamContext, taskEta, remediationChain }: {
  step: PlanStep;
  steps: PlanStep[];
  transition?: StatusTransition;
  projectId?: string;
  editingTaskId: string | null;
  deletingTaskId: string | null;
  onEdit: (taskId: string) => void;
  onDelete: (taskId: string) => void;
  onCancelAction: () => void;
  artifactEdges: ArtifactEdge[];
  upstreamContext?: UpstreamContext;
  taskEta?: TaskEta;
  remediationChain?: RemediationChain;
}): React.ReactElement {
  const accent = step.agent ? getAgentAccent(step.agent) : { color: 'var(--text-muted)', glow: 'transparent', bg: 'transparent' };
  const isActive = step.status === 'in_progress';
  const isDone = step.status === 'done';
  const isError = step.status === 'error';
  const isCancelled = step.status === 'cancelled';
  const isSkipped = step.status === 'skipped';
  const isPending = step.status === 'pending';
  const justCompleted = transition?.to === 'done';
  const justFailed = transition?.to === 'error';
  const justStarted = transition?.to === 'in_progress';

  const canEdit = isPending && !!projectId && !!step.taskId;
  const isEditing = editingTaskId === step.taskId;
  const isDeleting = deletingTaskId === step.taskId;

  const stepStatusClass = isActive ? 'planview-step--active'
    : isDone ? 'planview-step--done'
    : isError ? 'planview-step--error'
    : isCancelled ? 'planview-step--cancelled'
    : isSkipped ? 'planview-step--skipped'
    : 'planview-step--pending';

  const statusLabel = isDone ? 'completed' : isActive ? 'in progress' : isError ? 'failed' : isCancelled ? 'cancelled' : isSkipped ? 'skipped' : 'pending';

  return (
    <div
      className={`planview-step ${stepStatusClass} ${justStarted ? 'plan-fade-in' : ''} ${justFailed ? 'plan-error-shake' : ''} ${justCompleted ? 'plan-status-slide-in agent-done-flash' : ''}`}
      role="listitem"
      aria-label={`Task ${step.index}: ${step.text} — ${statusLabel}`}
    >
      <div className="planview-icon-slot">
        <StatusIcon status={step.status} agentName={step.agent} />
      </div>
      <div className={`planview-card ${isActive ? 'planview-card--active' : ''} ${canEdit ? 'planview-card--editable' : ''}`}
        style={{
          '--step-glow': accent.glow,
          background: isActive ? accent.bg : isError ? 'var(--glow-red)' : isCancelled ? 'rgba(160,160,160,0.05)' : isSkipped ? 'rgba(160,160,160,0.03)' : 'transparent',
          borderColor: isActive ? `${accent.color}30` : isError ? 'rgba(245,71,91,0.15)' : isCancelled ? 'rgba(160,160,160,0.1)' : isSkipped ? 'rgba(160,160,160,0.08)' : 'transparent',
        } as React.CSSProperties}>
        <div className="flex items-start gap-2">
          <p className={`text-sm leading-relaxed flex-1 ${isSkipped || isCancelled ? 'line-through' : ''}`}
            style={{
              color: isSkipped ? 'var(--text-muted)' : isDone ? 'var(--text-secondary)' : isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
              opacity: isSkipped ? 0.7 : 1,
              transition: 'color 0.3s ease',
            }}>
            {step.isRemediation && <span className="mr-1" title="Remediation task">🔧</span>}
            {step.text}
          </p>
          {canEdit && !isEditing && !isDeleting && (
            <div className="planview-step-actions flex items-center gap-1 flex-shrink-0 mt-0.5">
              <button
                onClick={() => onEdit(step.taskId!)}
                className="planview-icon-btn"
                aria-label={`Edit task ${step.taskId}`}
                title="Edit task"
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" />
                </svg>
              </button>
              <button
                onClick={() => onDelete(step.taskId!)}
                className="planview-icon-btn planview-icon-btn--danger"
                aria-label={`Delete task ${step.taskId}`}
                title="Remove task"
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M2 4h12M5 4V2h6v2M6 7v5M10 7v5M3 4l1 10h8l1-10" />
                </svg>
              </button>
            </div>
          )}
        </div>

        {isEditing && step.taskId && projectId && (
          <InlineEditForm
            taskId={step.taskId}
            currentGoal={step.text}
            projectId={projectId}
            onClose={onCancelAction}
          />
        )}

        {isDeleting && step.taskId && projectId && (
          <DeleteConfirmation
            taskId={step.taskId}
            projectId={projectId}
            onClose={onCancelAction}
          />
        )}

        {isError && step.failureReason && (
          <div className="planview-failure" role="alert">
            <span className="font-semibold">Error:</span>{' '}
            {step.failureReason.length > 150 ? `${step.failureReason.slice(0, 150)}…` : step.failureReason}
          </div>
        )}
        {step.dependsOn && step.dependsOn.length > 0 && <DependencyBadges dependsOn={step.dependsOn} steps={steps} />}
        {step.taskId && <ArtifactFlowIndicator edges={artifactEdges} taskId={step.taskId} />}
        {upstreamContext && <UpstreamContextSummary context={upstreamContext} />}
        {step.agent && (
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <span className="text-xs" aria-hidden="true">{AGENT_ICONS[step.agent] || '🔧'}</span>
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
              style={{
                background: isSkipped ? 'rgba(160,160,160,0.08)' : accent.bg,
                color: isSkipped ? 'var(--text-muted)' : accent.color,
                border: `1px solid ${isSkipped ? 'rgba(160,160,160,0.15)' : `${accent.color}20`}`,
              }}>
              {AGENT_LABELS[step.agent] || step.agent}
            </span>
            {step.taskId && <span className="text-[9px] font-mono opacity-40" style={{ color: 'var(--text-muted)' }}>{step.taskId}</span>}
            {isActive && <span className="text-[9px] font-bold tracking-wider plan-pulse plan-status-slide-in" style={{ color: accent.color, fontFamily: 'var(--font-mono)' }}>WORKING</span>}
            {isActive && taskEta && <EtaBadge eta={taskEta} />}
            {isDone && justCompleted && <span className="text-[9px] font-bold tracking-wider plan-complete-badge" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>✓ DONE</span>}
            {isDone && !justCompleted && <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>DONE</span>}
            {isError && <span className="text-[9px] font-bold tracking-wider plan-status-slide-in" style={{ color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>FAILED</span>}
            {isCancelled && <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>CANCELLED</span>}
            {isSkipped && <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>SKIPPED</span>}
            {isPending && taskEta && <EtaBadge eta={taskEta} />}
            {remediationChain && <RemediationBadge chain={remediationChain} />}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export default function PlanView({ activities, dagGraph, dagTaskStatus = {}, dagTaskFailureReasons = {}, projectId, isLoading, taskStartTimes }: Props): React.ReactElement {
  const isDagMode = !!(dagGraph?.tasks?.length);
  const prevStepsRef = useRef<Map<string, PlanStep['status']>>(new Map());
  const prevCompletedRef = useRef<number>(0);
  const [showCelebration, setShowCelebration] = useState(false);
  const celebrationShownRef = useRef(false);
  const [collapseCompleted, setCollapseCompleted] = useState(false);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const feedback = useFeedback({ enabled: true });

  const steps = useMemo(() => {
    if (dagGraph?.tasks?.length) return dagToPlanSteps(dagGraph, dagTaskStatus, dagTaskFailureReasons);
    return extractPlan(activities);
  }, [activities, dagGraph, dagTaskStatus, dagTaskFailureReasons]);

  const existingTaskIds = useMemo(() =>
    steps.filter(s => s.taskId).map(s => s.taskId!),
  [steps]);

  const artifactEdges = useMemo(() => buildArtifactEdges(steps), [steps]);
  const upstreamContexts = useMemo(() => buildUpstreamContexts(steps), [steps]);
  const taskEtas = useMemo(() => estimateTaskEtas(steps, taskStartTimes), [steps, taskStartTimes]);
  const remediationChains = useMemo(() => detectRemediationChains(steps), [steps]);
  const remediationChainMap = useMemo(() => {
    const map = new Map<string, RemediationChain>();
    for (const chain of remediationChains) {
      map.set(chain.originalTaskId, chain);
      for (const remId of chain.remediationTaskIds) {
        map.set(remId, chain);
      }
    }
    return map;
  }, [remediationChains]);

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

  const progress = useMemo(() => computeProgress(steps), [steps]);
  const { completed: completedCount, skipped: skippedCount, failed: errorCount, cancelled: cancelledCount, inProgress: inProgressCount, isAllDone, pct, hasFailures, actionable } = progress;
  const activeStep = steps.find(s => s.status === 'in_progress');
  const justCountChanged = completedCount !== prevCompletedRef.current && prevCompletedRef.current > 0;

  // Group steps by message round for rendering with dividers
  const stepGroups = useMemo(() => groupStepsByRound(steps), [steps]);
  const hasMultipleRounds = stepGroups.length > 1;

  // Close edit/delete forms when a task transitions away from pending
  useEffect(() => {
    if (editingTaskId && dagTaskStatus[editingTaskId] && dagTaskStatus[editingTaskId] !== 'pending') {
      setEditingTaskId(null);
    }
    if (deletingTaskId && dagTaskStatus[deletingTaskId] && dagTaskStatus[deletingTaskId] !== 'pending') {
      setDeletingTaskId(null);
    }
  }, [dagTaskStatus, editingTaskId, deletingTaskId]);

  const handleEdit = useCallback((taskId: string): void => {
    setEditingTaskId(taskId);
    setDeletingTaskId(null);
    setShowAddForm(false);
  }, []);

  const handleDelete = useCallback((taskId: string): void => {
    setDeletingTaskId(taskId);
    setEditingTaskId(null);
    setShowAddForm(false);
  }, []);

  const handleCancelAction = useCallback((): void => {
    setEditingTaskId(null);
    setDeletingTaskId(null);
  }, []);

  const handleToggleAddForm = useCallback((): void => {
    setShowAddForm(prev => !prev);
    setEditingTaskId(null);
    setDeletingTaskId(null);
  }, []);

  useEffect(() => {
    for (const [, t] of transitions) {
      if (t.to === 'done') feedback.onTaskComplete();
      else if (t.to === 'error') feedback.onTaskFailed();
      else if (t.to === 'in_progress') feedback.onTaskStarted();
    }
  }, [transitions, feedback]);

  // Celebration triggers only when ALL non-skipped tasks are complete
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
    if (isLoading) {
      return (
        <div className="flex flex-col items-center justify-center h-full px-4" role="status" aria-label="Loading plan">
          <div className="planview-loading-skeleton mb-4">
            <div className="planview-skeleton-bar planview-skeleton-bar--lg" />
            <div className="planview-skeleton-bar planview-skeleton-bar--md" />
            <div className="planview-skeleton-bar planview-skeleton-bar--sm" />
            <div className="planview-skeleton-bar planview-skeleton-bar--md" />
          </div>
          <div className="w-6 h-6 rounded-full border-2 border-t-transparent animate-spin mb-3"
            style={{ borderColor: 'var(--border-subtle)', borderTopColor: 'transparent' }} />
          <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>Loading plan…</p>
          <p className="text-xs mt-1 text-center" style={{ color: 'var(--text-muted)' }}>
            Waiting for the execution plan from the orchestrator
          </p>
        </div>
      );
    }
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

  const statusSummary = `Plan: ${completedCount} of ${actionable} tasks completed${
    skippedCount > 0 ? `, ${skippedCount} skipped` : ''}${
    inProgressCount > 0 ? `, ${inProgressCount} in progress` : ''}${
    errorCount > 0 ? `, ${errorCount} failed` : ''}`;

  const renderStep = (step: PlanStep): React.ReactElement => {
    const stepKey = step.taskId ?? `idx-${step.index}`;
    return (
      <StepRow
        key={stepKey}
        step={step}
        steps={steps}
        transition={transitions.get(stepKey)}
        projectId={projectId}
        editingTaskId={editingTaskId}
        deletingTaskId={deletingTaskId}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onCancelAction={handleCancelAction}
        artifactEdges={artifactEdges}
        upstreamContext={step.taskId ? upstreamContexts.get(step.taskId) : undefined}
        taskEta={step.taskId ? taskEtas.get(step.taskId) : undefined}
        remediationChain={step.taskId ? remediationChainMap.get(step.taskId) : undefined}
      />
    );
  };

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
          failed={errorCount} skipped={skippedCount} isAllDone={isAllDone} justChanged={justCountChanged} />
      </div>

      {completedCount >= 3 && steps.length > completedCount + skippedCount && (
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

      {/* Self-healing summary banner */}
      {remediationChains.length > 0 && (
        <div className="planview-healing-banner" role="status" aria-label="Self-healing activity">
          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{
            background: remediationChains.every(c => c.isHealed) ? 'var(--accent-green)' : 'var(--accent-amber)',
            animation: remediationChains.some(c => !c.isHealed) ? 'pulse 2s infinite' : 'none',
          }} />
          <span className="text-[10px] font-semibold" style={{
            color: remediationChains.every(c => c.isHealed) ? 'var(--accent-green)' : 'var(--accent-amber)',
            fontFamily: 'var(--font-mono)',
          }}>
            {remediationChains.every(c => c.isHealed)
              ? `🩹 ${remediationChains.length} issue${remediationChains.length > 1 ? 's' : ''} self-healed`
              : `🔧 ${remediationChains.filter(c => !c.isHealed).length} remediation${remediationChains.filter(c => !c.isHealed).length > 1 ? 's' : ''} in progress`
            }
          </span>
        </div>
      )}

      {/* Add Task Form (shown above timeline when open) */}
      {showAddForm && projectId && (
        <AddTaskForm
          projectId={projectId}
          existingTaskIds={existingTaskIds}
          onClose={() => setShowAddForm(false)}
        />
      )}

      {/* Timeline */}
      <div className="relative" role="list" aria-label="Execution plan tasks">
        <div className="planview-timeline-line" aria-hidden="true" />
        <div className="space-y-0">
          {hasMultipleRounds ? (
            // Render with round group dividers
            stepGroups.map((group, groupIdx) => {
              const visibleSteps = group.steps.filter(step => !(collapseCompleted && step.status === 'done'));
              if (visibleSteps.length === 0) return null;
              return (
                <div key={`round-${group.round}`}>
                  {groupIdx > 0 && <RoundDivider label={group.label} />}
                  {visibleSteps.map(renderStep)}
                </div>
              );
            })
          ) : (
            // Single round — no dividers needed
            steps.filter(step => !(collapseCompleted && step.status === 'done')).map(renderStep)
          )}
        </div>
      </div>

      {/* Add Task button (only in DAG mode with a project) */}
      {isDagMode && projectId && !isAllDone && (
        <button
          onClick={handleToggleAddForm}
          className="planview-add-task-btn"
          aria-label={showAddForm ? 'Close add task form' : 'Add a new task to the plan'}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            {showAddForm ? <path d="M4 4l8 8M12 4l-8 8" /> : <path d="M8 2v12M2 8h12" />}
          </svg>
          {showAddForm ? 'Cancel' : 'Add Task'}
        </button>
      )}

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
