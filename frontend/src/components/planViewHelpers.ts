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
}

export interface DagGraph {
  vision?: string;
  tasks?: DagTask[];
}

export interface PlanStep {
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done' | 'error' | 'cancelled';
  agent?: string;
  taskId?: string;
  dependsOn?: string[];
  failureReason?: string;
  isRemediation?: boolean;
}

export type StatusTransition = { from: PlanStep['status']; to: PlanStep['status'] };

// ============================================================================
// DAG → PlanStep conversion
// ============================================================================

export function dagToPlanSteps(
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

          steps.push({ index: steps.length + 1, text, status, agent, failureReason });
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
        steps.push({ index: steps.length + 1, text: a.task, status, agent, failureReason });
      }
    }
  }

  return steps;
}
