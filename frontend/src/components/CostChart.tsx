import { useEffect, useState } from 'react';
import { getCostBreakdown, type CostBreakdown } from '../api';
import { useAnimatedNumber, formatCost } from '../hooks/useAnimatedNumber';
import { getAgentAccent } from '../constants';
import { SkeletonBlock } from './Skeleton';

interface Props {
  projectId?: string;
}

/** Agent color lookup — falls back to orchestrator grey */
function agentColor(role: string): string {
  return getAgentAccent(role).color;
}

/** Format a day string (YYYY-MM-DD) into a short label (Mon, Tue, ...) */
function dayLabel(day: string): string {
  try {
    const d = new Date(day + 'T00:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'short' });
  } catch {
    return day.slice(-2); // fallback: last 2 chars
  }
}

// ── Loading skeleton ──────────────────────────────────────────
function CostChartSkeleton() {
  return (
    <div className="space-y-4" role="presentation" aria-hidden="true">
      {/* Summary row skeleton */}
      <div className="flex items-center gap-6">
        <SkeletonBlock width="80px" height="28px" />
        <SkeletonBlock width="60px" height="14px" />
        <SkeletonBlock width="70px" height="14px" />
      </div>
      {/* Chart bars skeleton */}
      <div className="flex items-end gap-2 h-24">
        {[40, 65, 30, 80, 55, 45, 70].map((h, i) => (
          <div key={i} className="flex-1 flex flex-col items-center gap-1">
            <SkeletonBlock width="100%" height={`${h}%`} className="rounded-t-md" />
            <SkeletonBlock width="24px" height="10px" />
          </div>
        ))}
      </div>
      {/* Agent breakdown skeleton */}
      <div className="space-y-2">
        {[1, 2, 3].map(i => (
          <div key={i} className="flex items-center gap-2">
            <SkeletonBlock width="8px" height="8px" className="rounded-full" />
            <SkeletonBlock width="80px" height="12px" />
            <SkeletonBlock width="100%" height="6px" className="rounded-full" />
            <SkeletonBlock width="40px" height="12px" />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <div
        className="w-12 h-12 rounded-xl flex items-center justify-center text-xl mb-3"
        style={{ background: 'var(--bg-elevated)' }}
      >
        📊
      </div>
      <p className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
        No cost data yet
      </p>
      <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
        Run some agents and cost data will appear here
      </p>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────
export default function CostChart({ projectId }: Props) {
  const [data, setData] = useState<CostBreakdown | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const result = await getCostBreakdown(projectId, 7);
        if (!cancelled) setData(result);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load cost data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [projectId]);

  // Animated total cost
  const animatedTotal = useAnimatedNumber(data?.total_cost ?? 0, 700, data?.total_cost && data.total_cost < 1 ? 3 : 2);

  // ── Loading ──
  if (loading) return <CostChartSkeleton />;

  // ── Error ──
  if (error) {
    return (
      <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--accent-red)' }}>
        <span>⚠️</span>
        <span>{error}</span>
      </div>
    );
  }

  // ── Empty ──
  if (!data || (data.total_runs === 0 && data.total_cost === 0)) {
    return <EmptyState />;
  }

  const { by_day, by_agent, total_cost, total_runs } = data;
  const avgCostPerRun = total_runs > 0 ? total_cost / total_runs : 0;
  const maxDayCost = Math.max(...by_day.map(d => d.cost), 0.001); // avoid /0

  return (
    <div className="space-y-5">
      {/* ── Summary row ── */}
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <span className="text-2xl font-bold tabular-nums" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-display)' }}>
          ${animatedTotal}
        </span>
        <span className="text-xs tabular-nums" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          {total_runs} run{total_runs !== 1 ? 's' : ''}
        </span>
        <span className="text-xs tabular-nums" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          avg {formatCost(avgCostPerRun)}/run
        </span>
      </div>

      {/* ── Daily bar chart (CSS-only) ── */}
      {by_day.length > 0 && (
        <div>
          <p
            className="text-[10px] font-bold tracking-[0.12em] uppercase mb-3"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
          >
            Daily Cost — Last 7 Days
          </p>
          <div className="flex items-end gap-1.5" style={{ height: '100px' }}>
            {by_day.map((day) => {
              const pct = Math.max((day.cost / maxDayCost) * 100, 2); // min 2% for visibility
              return (
                <div
                  key={day.day}
                  className="flex-1 flex flex-col items-center gap-1 group"
                  title={`${day.day}: ${formatCost(day.cost)} (${day.runs} run${day.runs !== 1 ? 's' : ''})`}
                >
                  {/* Cost label on hover */}
                  <span
                    className="text-[9px] opacity-0 group-hover:opacity-100 transition-opacity tabular-nums"
                    style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}
                  >
                    {formatCost(day.cost)}
                  </span>
                  {/* Bar */}
                  <div className="w-full flex-1 flex items-end">
                    <div
                      className="w-full rounded-t-md transition-all duration-500 group-hover:opacity-100"
                      style={{
                        height: `${pct}%`,
                        background: 'linear-gradient(to top, var(--accent-blue), rgba(99,140,255,0.5))',
                        opacity: 0.75,
                        boxShadow: '0 0 8px rgba(99,140,255,0.15)',
                      }}
                    />
                  </div>
                  {/* Day label */}
                  <span
                    className="text-[10px]"
                    style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                  >
                    {dayLabel(day.day)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Per-agent breakdown ── */}
      {by_agent.length > 0 && (
        <div>
          <p
            className="text-[10px] font-bold tracking-[0.12em] uppercase mb-3"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
          >
            By Agent
          </p>
          <div className="space-y-2">
            {by_agent
              .sort((a, b) => b.cost - a.cost)
              .map((agent) => {
                const pct = total_cost > 0 ? (agent.cost / total_cost) * 100 : 0;
                const color = agentColor(agent.agent_role);
                return (
                  <div key={agent.agent_role} className="flex items-center gap-2.5 group">
                    {/* Color dot */}
                    <div
                      className="w-2 h-2 rounded-full flex-shrink-0"
                      style={{ background: color }}
                    />
                    {/* Agent name */}
                    <span
                      className="text-xs w-28 truncate flex-shrink-0"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {agent.agent_role.replace(/_/g, ' ')}
                    </span>
                    {/* Horizontal bar */}
                    <div
                      className="flex-1 h-1.5 rounded-full overflow-hidden"
                      style={{ background: 'var(--border-dim)' }}
                    >
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{
                          width: `${Math.max(pct, 1)}%`,
                          background: color,
                          boxShadow: `0 0 6px ${color}40`,
                        }}
                      />
                    </div>
                    {/* Cost + runs */}
                    <span
                      className="text-[11px] w-16 text-right flex-shrink-0 tabular-nums"
                      style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                    >
                      {formatCost(agent.cost)}
                    </span>
                    <span
                      className="text-[10px] w-10 text-right flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity tabular-nums"
                      style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                    >
                      {agent.runs}×
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}
    </div>
  );
}
