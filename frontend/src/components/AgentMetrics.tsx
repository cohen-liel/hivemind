import { useState, useCallback } from 'react';
import type { AgentMetric } from '../hooks/useAgentMetrics';
import { AGENT_ICONS, AGENT_LABELS } from '../constants';

interface AgentMetricsProps {
  /** Per-agent performance metrics */
  metrics: AgentMetric[];
}

/** Format seconds into a human-readable duration string */
function formatDuration(seconds: number): string {
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

/** Format USD cost to appropriate precision */
function formatCost(cost: number): string {
  if (cost === 0) return '$0';
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

/** Stat card for individual metrics */
function StatBadge({ label, value, color }: { label: string; value: string; color: string }): React.ReactElement {
  return (
    <div
      className="flex flex-col items-center px-2 py-1.5 rounded-lg min-w-[60px]"
      style={{ background: 'var(--bg-elevated)' }}
    >
      <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
        {label}
      </span>
      <span className="text-xs font-bold tabular-nums" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

/** Success rate bar visualization */
function SuccessBar({ rate }: { rate: number }): React.ReactElement {
  const pct = Math.round(rate * 100);
  const barColor = pct >= 80 ? 'var(--accent-green)' : pct >= 50 ? 'var(--accent-amber)' : 'var(--accent-red)';

  return (
    <div className="flex items-center gap-2 w-full">
      <div
        className="flex-1 h-1.5 rounded-full overflow-hidden"
        style={{ background: 'var(--bg-void)' }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Success rate: ${pct}%`}
      >
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      <span className="text-[10px] font-mono font-bold tabular-nums w-8 text-right" style={{ color: barColor }}>
        {pct}%
      </span>
    </div>
  );
}

/** Single agent row in the metrics panel */
function AgentRow({ metric }: { metric: AgentMetric }): React.ReactElement {
  const icon = AGENT_ICONS[metric.agent] ?? '\u{1F916}';
  const label = AGENT_LABELS[metric.agent] ?? metric.agent;
  const totalTasks = metric.tasksCompleted + metric.tasksFailed;

  return (
    <div
      className="rounded-xl p-3 transition-all hover:scale-[1.01]"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-dim)',
      }}
    >
      {/* Agent header */}
      <div className="flex items-center gap-2 mb-2">
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center text-sm flex-shrink-0"
          style={{ background: 'var(--bg-elevated)' }}
        >
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
            {label}
          </span>
          <span className="text-[10px] ml-1.5" style={{ color: 'var(--text-muted)' }}>
            {totalTasks} task{totalTasks !== 1 ? 's' : ''}
          </span>
        </div>
        <span className="text-xs font-bold font-mono tabular-nums" style={{ color: 'var(--accent-blue)' }}>
          {formatCost(metric.totalCost)}
        </span>
      </div>

      {/* Success rate bar */}
      <SuccessBar rate={metric.successRate} />

      {/* Stats row */}
      <div className="flex gap-1.5 mt-2 overflow-x-auto">
        <StatBadge label="Avg" value={formatDuration(metric.avgDuration)} color="var(--text-secondary)" />
        <StatBadge label="Total" value={formatDuration(metric.totalDuration)} color="var(--text-secondary)" />
        <StatBadge label="Turns" value={String(metric.totalTurns)} color="var(--accent-purple)" />
        <StatBadge
          label="Pass"
          value={String(metric.tasksCompleted)}
          color="var(--accent-green)"
        />
        {metric.tasksFailed > 0 && (
          <StatBadge
            label="Fail"
            value={String(metric.tasksFailed)}
            color="var(--accent-red)"
          />
        )}
      </div>
    </div>
  );
}

/**
 * AgentMetrics — Collapsible panel displaying per-agent performance statistics.
 * Computes averages, totals, and success rates from WebSocket agent events.
 *
 * Features:
 * - Collapsible header with summary stats
 * - Per-agent rows with cost, duration, success rate, and task counts
 * - Responsive grid layout
 * - Keyboard accessible (Enter/Space to toggle)
 */
export default function AgentMetrics({ metrics }: AgentMetricsProps): React.ReactElement | null {
  const [collapsed, setCollapsed] = useState<boolean>(true);

  const toggleCollapsed = useCallback((): void => {
    setCollapsed(prev => !prev);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setCollapsed(prev => !prev);
    }
  }, []);

  // Don't render if no metrics available
  if (metrics.length === 0) return null;

  // Compute aggregate summary
  const totalCost = metrics.reduce((sum, m) => sum + m.totalCost, 0);
  const totalTasks = metrics.reduce((sum, m) => sum + m.tasksCompleted + m.tasksFailed, 0);
  const overallSuccessRate = totalTasks > 0
    ? metrics.reduce((sum, m) => sum + m.tasksCompleted, 0) / totalTasks
    : 0;

  return (
    <div
      className="rounded-xl overflow-hidden transition-all"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-dim)',
      }}
    >
      {/* Collapsible header */}
      <button
        onClick={toggleCollapsed}
        onKeyDown={handleKeyDown}
        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors focus:outline-none focus-visible:ring-2"
        style={{ background: 'transparent' }}
        aria-expanded={!collapsed}
        aria-controls="agent-metrics-content"
        aria-label="Toggle agent performance metrics"
      >
        {/* Icon */}
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center text-sm flex-shrink-0"
          style={{ background: 'var(--glow-blue)' }}
        >
          📊
        </div>

        {/* Title + summary */}
        <div className="flex-1 min-w-0">
          <h3
            className="text-xs font-semibold uppercase tracking-wide"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
          >
            Agent Performance
          </h3>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-[10px] font-mono tabular-nums" style={{ color: 'var(--accent-blue)' }}>
              {formatCost(totalCost)}
            </span>
            <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              {totalTasks} tasks
            </span>
            <span
              className="text-[10px] font-mono tabular-nums"
              style={{ color: overallSuccessRate >= 0.8 ? 'var(--accent-green)' : 'var(--accent-amber)' }}
            >
              {Math.round(overallSuccessRate * 100)}% pass
            </span>
          </div>
        </div>

        {/* Chevron */}
        <svg
          className={`w-4 h-4 flex-shrink-0 transition-transform duration-200 ${collapsed ? '' : 'rotate-180'}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
          style={{ color: 'var(--text-muted)' }}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expandable content */}
      {!collapsed && (
        <div
          id="agent-metrics-content"
          className="px-4 pb-4 space-y-2 animate-[fadeSlideIn_0.2s_ease-out]"
          role="region"
          aria-label="Agent performance details"
        >
          {/* Divider */}
          <div className="h-px w-full" style={{ background: 'var(--border-dim)' }} />

          {/* Agent rows — responsive grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {metrics.map(metric => (
              <AgentRow key={metric.agent} metric={metric} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
