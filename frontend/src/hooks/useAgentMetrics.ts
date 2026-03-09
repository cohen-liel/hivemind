import { useMemo } from 'react';
import type { ActivityEntry } from '../types';

/** Per-agent performance metrics computed from activity events */
export interface AgentMetric {
  /** Agent role name */
  agent: string;
  /** Number of successfully completed tasks */
  tasksCompleted: number;
  /** Number of failed tasks */
  tasksFailed: number;
  /** Total cost in USD */
  totalCost: number;
  /** Total duration in seconds */
  totalDuration: number;
  /** Average duration per task in seconds */
  avgDuration: number;
  /** Success rate as a decimal (0-1) */
  successRate: number;
  /** Total turns used */
  totalTurns: number;
}

/**
 * Computes per-agent performance metrics from activity feed entries.
 * Processes agent_started and agent_finished events to calculate:
 * - Task completion counts (success/failure)
 * - Total and average cost
 * - Total and average duration
 * - Success rate
 *
 * @param activities - Array of activity feed entries from WebSocket events
 * @returns Sorted array of per-agent metrics (by total cost descending)
 */
export function useAgentMetrics(activities: ActivityEntry[]): AgentMetric[] {
  return useMemo(() => {
    const metricsMap = new Map<string, {
      completed: number;
      failed: number;
      totalCost: number;
      totalDuration: number;
      totalTurns: number;
    }>();

    for (const entry of activities) {
      if (entry.type !== 'agent_finished' || !entry.agent) continue;

      const existing = metricsMap.get(entry.agent) ?? {
        completed: 0,
        failed: 0,
        totalCost: 0,
        totalDuration: 0,
        totalTurns: 0,
      };

      if (entry.is_error) {
        existing.failed++;
      } else {
        existing.completed++;
      }

      existing.totalCost += entry.cost ?? 0;
      existing.totalDuration += entry.duration ?? 0;
      existing.totalTurns += entry.turns ?? 0;

      metricsMap.set(entry.agent, existing);
    }

    const metrics: AgentMetric[] = [];
    for (const [agent, data] of metricsMap) {
      const total = data.completed + data.failed;
      metrics.push({
        agent,
        tasksCompleted: data.completed,
        tasksFailed: data.failed,
        totalCost: data.totalCost,
        totalDuration: data.totalDuration,
        avgDuration: total > 0 ? data.totalDuration / total : 0,
        successRate: total > 0 ? data.completed / total : 0,
        totalTurns: data.totalTurns,
      });
    }

    // Sort by total cost descending (most expensive agents first)
    metrics.sort((a, b) => b.totalCost - a.totalCost);
    return metrics;
  }, [activities]);
}
