import type { ActivityEntry } from '../types';

// ============================================================================
// Types
// ============================================================================

export interface DagTask {
  id: string;
  role: string;
  goal: string;
  depends_on?: string[];
  is_remediation?: boolean;
  message_round?: number;
}

export interface DagGraph {
  vision?: string;
  tasks?: DagTask[];
}

export interface PlanStep {
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done' | 'error' | 'cancelled' | 'skipped';
  agent?: string;
  taskId?: string;
  dependsOn?: string[];
  failureReason?: string;
  isRemediation?: boolean;
  messageRound: number;
  skipReason?: string;
}

export type StatusTransition = { from: PlanStep['status']; to: PlanStep['status'] };

/** An artifact edge representing data flow between two connected tasks. */
export interface ArtifactEdge {
  fromTaskId: string;
  toTaskId: string;
  /** Status of the artifact transfer. */
  status: 'received' | 'missing' | 'partial';
}

/** Upstream context summary for a task. */
export interface UpstreamContext {
  taskId: string;
  /** Tasks this depends on with their status and goal summary. */
  upstreamTasks: Array<{
    taskId: string;
    role: string;
    goalSummary: string;
    status: PlanStep['status'];
  }>;
}

/** ETA estimate for a task based on historical role averages. */
export interface TaskEta {
  taskId: string;
  /** Estimated remaining seconds, or null if cannot estimate. */
  etaSeconds: number | null;
  /** Display-friendly ETA string. */
  etaDisplay: string;
}

/** A chain of remediation tasks linked to their failed originals. */
export interface RemediationChain {
  originalTaskId: string;
  remediationTaskIds: string[];
  isHealed: boolean;
}

/** A group of steps originating from the same message round. */
export interface StepGroup {
  round: number;
  label: string;
  steps: PlanStep[];
}

// ============================================================================
// DAG → PlanStep conversion
// ============================================================================

/** Extract numeric suffix from a task ID like "task_003" → 3, or return 0. */
function parseTaskIdNumber(id: string): number {
  const match = id.match(/_(\d+)$/);
  return match ? parseInt(match[1], 10) : 0;
}

/**
 * Infer message round numbers for tasks that don't have an explicit message_round.
 * Groups consecutive task IDs together; a gap > 1 in the numeric suffix starts a new round.
 * Remediation tasks are grouped with the round that precedes them.
 */
function inferMessageRounds(tasks: DagTask[]): Map<string, number> {
  const rounds = new Map<string, number>();
  if (tasks.length === 0) return rounds;

  // If ALL tasks have explicit message_round, just use those
  if (tasks.every(t => t.message_round !== undefined && t.message_round !== null)) {
    for (const t of tasks) {
      rounds.set(t.id, t.message_round!);
    }
    return rounds;
  }

  // Infer rounds from task ID numbering gaps
  let currentRound = 1;
  let prevNum = -1;

  for (const task of tasks) {
    // If the task has an explicit round, use it and update the current round tracker
    if (task.message_round !== undefined && task.message_round !== null) {
      currentRound = task.message_round;
      rounds.set(task.id, currentRound);
      prevNum = parseTaskIdNumber(task.id);
      continue;
    }

    const num = parseTaskIdNumber(task.id);

    // A gap > 1 in task numbering indicates tasks were added in a later round
    if (prevNum >= 0 && num > prevNum + 1 && !task.is_remediation) {
      currentRound++;
    }

    rounds.set(task.id, currentRound);
    prevNum = num;
  }

  return rounds;
}

export function dagToPlanSteps(
  graph: DagGraph,
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled' | 'skipped'>,
  failureReasons: Record<string, string>,
): PlanStep[] {
  const tasks = graph.tasks ?? [];
  const roundMap = inferMessageRounds(tasks);

  return tasks.map((task, i) => {
    const taskStatus = dagTaskStatus[task.id] ?? 'pending';
    const planStatus: PlanStep['status'] =
      taskStatus === 'completed' ? 'done' :
      taskStatus === 'working' ? 'in_progress' :
      taskStatus === 'failed' ? 'error' :
      taskStatus === 'cancelled' ? 'cancelled' :
      taskStatus === 'skipped' ? 'skipped' :
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
      messageRound: roundMap.get(task.id) ?? 1,
    };
  });
}

// ============================================================================
// Grouping by message round
// ============================================================================

/** Group plan steps by their originating message round. */
export function groupStepsByRound(steps: PlanStep[]): StepGroup[] {
  if (steps.length === 0) return [];

  const groupMap = new Map<number, PlanStep[]>();
  for (const step of steps) {
    const round = step.messageRound;
    if (!groupMap.has(round)) groupMap.set(round, []);
    groupMap.get(round)!.push(step);
  }

  const sortedRounds = [...groupMap.keys()].sort((a, b) => a - b);
  return sortedRounds.map((round, idx) => ({
    round,
    label: idx === 0 ? 'Initial plan' : `Added in message #${round}`,
    steps: groupMap.get(round)!,
  }));
}

// ============================================================================
// Progress counters
// ============================================================================

export interface PlanProgress {
  total: number;
  completed: number;
  skipped: number;
  failed: number;
  cancelled: number;
  inProgress: number;
  /** Total non-skipped tasks (the denominator for progress). */
  actionable: number;
  /** True when all non-skipped tasks are done. */
  isAllDone: boolean;
  /** Percentage of actionable tasks completed. */
  pct: number;
  hasFailures: boolean;
}

export function computeProgress(steps: PlanStep[]): PlanProgress {
  const total = steps.length;
  const completed = steps.filter(s => s.status === 'done').length;
  const skipped = steps.filter(s => s.status === 'skipped').length;
  const failed = steps.filter(s => s.status === 'error').length;
  const cancelled = steps.filter(s => s.status === 'cancelled').length;
  const inProgress = steps.filter(s => s.status === 'in_progress').length;
  const actionable = total - skipped;
  const isAllDone = actionable > 0 && completed === actionable;
  const pct = actionable > 0 ? Math.round((completed / actionable) * 100) : 0;
  const hasFailures = failed > 0 || cancelled > 0;

  return { total, completed, skipped, failed, cancelled, inProgress, actionable, isAllDone, pct, hasFailures };
}

// ============================================================================
// Fallback: parse orchestrator output for numbered steps
// ============================================================================

export function extractPlan(activities: ActivityEntry[]): PlanStep[] {
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

          steps.push({ index: steps.length + 1, text, status, agent, failureReason, messageRound: 1 });
        }
      }
    }
  }

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
        steps.push({ index: steps.length + 1, text: a.task, status, agent, failureReason, messageRound: 1 });
      }
    }
  }

  return steps;
}

// ============================================================================
// Artifact flow edges between dependent tasks
// ============================================================================

/** Build artifact flow edges based on task dependencies and their statuses. */
export function buildArtifactEdges(
  steps: PlanStep[],
): ArtifactEdge[] {
  const statusMap = new Map(steps.map(s => [s.taskId, s.status]));
  const edges: ArtifactEdge[] = [];

  for (const step of steps) {
    if (!step.dependsOn || !step.taskId) continue;
    for (const depId of step.dependsOn) {
      const depStatus = statusMap.get(depId);
      let edgeStatus: ArtifactEdge['status'] = 'missing';
      if (depStatus === 'done') {
        edgeStatus = 'received';
      } else if (depStatus === 'in_progress') {
        edgeStatus = 'partial';
      }
      edges.push({ fromTaskId: depId, toTaskId: step.taskId, status: edgeStatus });
    }
  }
  return edges;
}

// ============================================================================
// Upstream context for each task
// ============================================================================

/** Build upstream context summaries showing what each task consumes. */
export function buildUpstreamContexts(steps: PlanStep[]): Map<string, UpstreamContext> {
  const stepMap = new Map(steps.map(s => [s.taskId, s]));
  const contexts = new Map<string, UpstreamContext>();

  for (const step of steps) {
    if (!step.taskId || !step.dependsOn || step.dependsOn.length === 0) continue;

    const upstreamTasks: UpstreamContext['upstreamTasks'] = [];
    for (const depId of step.dependsOn) {
      const dep = stepMap.get(depId);
      if (dep) {
        upstreamTasks.push({
          taskId: depId,
          role: dep.agent ?? 'unknown',
          goalSummary: dep.text.length > 60 ? dep.text.slice(0, 57) + '…' : dep.text,
          status: dep.status,
        });
      }
    }

    if (upstreamTasks.length > 0) {
      contexts.set(step.taskId, { taskId: step.taskId, upstreamTasks });
    }
  }
  return contexts;
}

// ============================================================================
// Progress estimation based on role averages
// ============================================================================

/** Average task duration per role in seconds (historical defaults). */
const ROLE_AVG_DURATION: Record<string, number> = {
  pm: 45,
  researcher: 90,
  frontend_developer: 120,
  backend_developer: 120,
  database_expert: 90,
  devops: 60,
  test_engineer: 90,
  reviewer: 60,
  security_auditor: 75,
  developer: 120,
};

/** Format seconds into a human-friendly ETA string. */
function formatEta(seconds: number): string {
  if (seconds <= 0) return '<1m';
  if (seconds < 60) return `~${Math.ceil(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainMins = minutes % 60;
  return remainMins > 0 ? `~${hours}h ${remainMins}m` : `~${hours}h`;
}

/** Estimate ETAs for pending and in-progress tasks. */
export function estimateTaskEtas(
  steps: PlanStep[],
  taskStartTimes?: Record<string, number>,
): Map<string, TaskEta> {
  const etas = new Map<string, TaskEta>();
  const now = Date.now() / 1000;

  for (const step of steps) {
    if (!step.taskId) continue;
    if (step.status === 'done' || step.status === 'skipped' || step.status === 'cancelled') continue;

    const avgDuration = ROLE_AVG_DURATION[step.agent ?? ''] ?? 90;

    if (step.status === 'in_progress') {
      const startTime = taskStartTimes?.[step.taskId];
      if (startTime) {
        const elapsed = now - startTime;
        const remaining = Math.max(0, avgDuration - elapsed);
        etas.set(step.taskId, {
          taskId: step.taskId,
          etaSeconds: remaining,
          etaDisplay: formatEta(remaining),
        });
      } else {
        etas.set(step.taskId, {
          taskId: step.taskId,
          etaSeconds: avgDuration,
          etaDisplay: formatEta(avgDuration),
        });
      }
    } else if (step.status === 'pending') {
      // For pending tasks, estimate starts after dependencies complete
      const depEtaMax = (step.dependsOn ?? []).reduce((maxEta, depId) => {
        const depEta = etas.get(depId);
        return Math.max(maxEta, depEta?.etaSeconds ?? 0);
      }, 0);
      const totalEta = depEtaMax + avgDuration;
      etas.set(step.taskId, {
        taskId: step.taskId,
        etaSeconds: totalEta,
        etaDisplay: formatEta(totalEta),
      });
    }
  }
  return etas;
}

// ============================================================================
// Remediation chain detection
// ============================================================================

/** Detect remediation chains — tasks that are self-healing retries of failed originals. */
export function detectRemediationChains(steps: PlanStep[]): RemediationChain[] {
  const chains = new Map<string, RemediationChain>();

  for (const step of steps) {
    if (!step.isRemediation || !step.taskId || !step.dependsOn) continue;

    // Find the original task this remediates (the failed dependency)
    for (const depId of step.dependsOn) {
      const depStep = steps.find(s => s.taskId === depId);
      if (!depStep) continue;

      // The original is either the dep itself (if it failed) or the chain head
      const originalId = depStep.isRemediation
        ? (chains.get(depId)?.originalTaskId ?? depId)
        : depId;

      if (!chains.has(originalId)) {
        chains.set(originalId, {
          originalTaskId: originalId,
          remediationTaskIds: [],
          isHealed: false,
        });
      }
      const chain = chains.get(originalId)!;
      if (!chain.remediationTaskIds.includes(step.taskId)) {
        chain.remediationTaskIds.push(step.taskId);
      }
      chain.isHealed = step.status === 'done';
    }
  }

  return Array.from(chains.values());
}
