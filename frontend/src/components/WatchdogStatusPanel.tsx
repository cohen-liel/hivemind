import { useEffect, useState, useCallback, memo } from 'react';
import { useWSSubscribe } from '../WebSocketContext';
import { useToast } from './Toast';
import type { WSEvent } from '../types';

/* ─── Types mirroring src/api/health.py Pydantic models ─── */

interface TestFailure {
  test_name: string;
  message: string;
}

interface SuiteResult {
  suite: string;
  status: string;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  errors: number;
  failures: TestFailure[];
  coverage_pct: number | null;
  duration_seconds: number;
  error_message: string | null;
}

interface TestRunResult {
  run_id: string;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number;
  suites: SuiteResult[];
  summary: string;
}

interface TestHealthResponse {
  status: string;
  last_run: TestRunResult | null;
  recent_runs: TestRunResult[];
  scheduler_active: boolean;
  scheduler_interval_seconds: number;
  is_running: boolean;
}

/* ─── Auth helper (mirrors api.ts pattern) ─── */

function getAuthHeaders(): Record<string, string> {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="hivemind-auth-token"]');
  const token = meta?.content || '';
  const stored = token || (() => { try { return localStorage.getItem('hivemind-auth-token') || ''; } catch { return ''; } })();
  return stored ? { 'X-API-Key': stored, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

/* ─── Status helpers ─── */

type HealthStatus = 'healthy' | 'failing' | 'unknown' | 'running';

function getStatusColor(status: HealthStatus): string {
  switch (status) {
    case 'healthy': return 'var(--accent-green)';
    case 'failing': return 'var(--accent-red)';
    case 'running': return 'var(--accent-blue)';
    default: return 'var(--text-muted)';
  }
}

function getStatusBg(status: HealthStatus): string {
  switch (status) {
    case 'healthy': return 'var(--glow-green)';
    case 'failing': return 'var(--glow-red)';
    case 'running': return 'var(--glow-blue)';
    default: return 'var(--bg-elevated)';
  }
}

function getStatusLabel(status: HealthStatus): string {
  switch (status) {
    case 'healthy': return 'Passing';
    case 'failing': return 'Failing';
    case 'running': return 'Running';
    default: return 'Unknown';
  }
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const min = Math.floor(seconds / 60);
  const sec = (seconds % 60).toFixed(0);
  return `${min}m ${sec}s`;
}

/* ─── Sub-components ─── */

const StatusBadge = memo(function StatusBadge({ status }: { status: HealthStatus }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold"
      style={{
        background: getStatusBg(status),
        color: getStatusColor(status),
        border: `1px solid ${getStatusColor(status)}`,
        borderColor: `color-mix(in srgb, ${getStatusColor(status)} 30%, transparent)`,
      }}
      role="status"
      aria-label={`Test status: ${getStatusLabel(status)}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${status === 'running' ? 'animate-pulse' : ''}`}
        style={{ background: getStatusColor(status) }}
      />
      {getStatusLabel(status)}
    </span>
  );
});

const CoverageBar = memo(function CoverageBar({ pct }: { pct: number }) {
  const color = pct >= 80 ? 'var(--accent-green)' : pct >= 60 ? 'var(--accent-amber)' : 'var(--accent-red)';
  return (
    <div className="flex items-center gap-2.5">
      <div
        className="flex-1 h-2 rounded-full overflow-hidden"
        style={{ background: 'var(--bg-elevated)' }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Code coverage: ${pct.toFixed(1)}%`}
      >
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${Math.min(pct, 100)}%`,
            background: `linear-gradient(90deg, ${color}, color-mix(in srgb, ${color} 70%, white))`,
            boxShadow: `0 0 8px ${color}`,
          }}
        />
      </div>
      <span className="text-xs font-mono flex-shrink-0" style={{ color, fontFamily: 'var(--font-mono)' }}>
        {pct.toFixed(1)}%
      </span>
    </div>
  );
});

const SuiteCard = memo(function SuiteCard({ suite }: { suite: SuiteResult }) {
  const [expanded, setExpanded] = useState(false);
  const hasFailures = suite.failures.length > 0;
  const statusColor = suite.status === 'passed' ? 'var(--accent-green)' : suite.status === 'failed' ? 'var(--accent-red)' : 'var(--accent-amber)';

  return (
    <div
      className="rounded-xl p-3"
      style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {suite.suite}
          </span>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-md font-semibold"
            style={{ background: `color-mix(in srgb, ${statusColor} 15%, transparent)`, color: statusColor }}
          >
            {suite.status.toUpperCase()}
          </span>
        </div>
        <span className="text-[11px]" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          {formatDuration(suite.duration_seconds)}
        </span>
      </div>

      {/* Test counts */}
      <div className="flex items-center gap-3 mb-2">
        <span className="text-xs" style={{ color: 'var(--accent-green)' }}>✓ {suite.passed}</span>
        <span className="text-xs" style={{ color: suite.failed > 0 ? 'var(--accent-red)' : 'var(--text-muted)' }}>✗ {suite.failed}</span>
        {suite.skipped > 0 && (
          <span className="text-xs" style={{ color: 'var(--accent-amber)' }}>⊘ {suite.skipped}</span>
        )}
        <span className="text-xs ml-auto" style={{ color: 'var(--text-muted)' }}>{suite.total} total</span>
      </div>

      {/* Coverage */}
      {suite.coverage_pct !== null && (
        <CoverageBar pct={suite.coverage_pct} />
      )}

      {/* Error message */}
      {suite.error_message && (
        <div className="mt-2 text-xs p-2 rounded-lg" style={{ background: 'var(--glow-red)', color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
          {suite.error_message}
        </div>
      )}

      {/* Expandable failures */}
      {hasFailures && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-xs font-medium transition-colors w-full"
            style={{ color: 'var(--accent-red)' }}
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Hide' : 'Show'} ${suite.failures.length} failure details`}
          >
            <svg
              width="10" height="10" viewBox="0 0 10 10"
              className={`transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
            >
              <path d="M3 1l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
            </svg>
            {suite.failures.length} failure{suite.failures.length !== 1 ? 's' : ''}
          </button>
          {expanded && (
            <div className="mt-2 space-y-1.5 max-h-48 overflow-y-auto" role="list" aria-label="Test failures">
              {suite.failures.map((f, i) => (
                <div
                  key={i}
                  className="text-[11px] p-2 rounded-lg"
                  style={{ background: 'rgba(245, 71, 91, 0.06)', border: '1px solid rgba(245, 71, 91, 0.1)' }}
                  role="listitem"
                >
                  <div className="font-semibold truncate" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                    {f.test_name}
                  </div>
                  {f.message && (
                    <div className="mt-0.5 break-words" style={{ color: 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
                      {f.message}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
});

/* ─── Main Component ─── */

export default function WatchdogStatusPanel(): JSX.Element {
  const [health, setHealth] = useState<TestHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/api/health/tests', { headers: getAuthHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TestHealthResponse = await res.json();
      setHealth(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch test health');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  // Subscribe to WebSocket watchdog_report events
  const handleWSEvent = useCallback((event: WSEvent) => {
    if ((event as unknown as Record<string, unknown>).type === 'watchdog_report') {
      // A new test run result arrived — refresh health data
      fetchHealth();
    }
  }, [fetchHealth]);

  useWSSubscribe(handleWSEvent);

  const triggerRun = async (): Promise<void> => {
    setTriggering(true);
    try {
      const res = await fetch('/api/health/tests/run', {
        method: 'POST',
        headers: getAuthHeaders(),
      });
      if (res.status === 409) {
        toast.warning('A test run is already in progress');
      } else if (res.status === 202) {
        toast.success('Test run started');
        // Update local state to show running
        setHealth(prev => prev ? { ...prev, is_running: true } : prev);
      } else {
        const body = await res.json().catch(() => ({}));
        toast.error(body.detail || `Failed to trigger tests (${res.status})`);
      }
    } catch {
      toast.error('Failed to connect to backend');
    } finally {
      setTriggering(false);
    }
  };

  const displayStatus: HealthStatus = health?.is_running ? 'running' : (health?.status as HealthStatus) || 'unknown';

  // Aggregate counts from last run
  const lastRun = health?.last_run ?? null;
  const totalPassed = lastRun?.suites.reduce((sum, s) => sum + s.passed, 0) ?? 0;
  const totalFailed = lastRun?.suites.reduce((sum, s) => sum + s.failed, 0) ?? 0;
  const totalTests = lastRun?.suites.reduce((sum, s) => sum + s.total, 0) ?? 0;
  const avgCoverage = (() => {
    if (!lastRun) return null;
    const withCoverage = lastRun.suites.filter(s => s.coverage_pct !== null);
    if (withCoverage.length === 0) return null;
    return withCoverage.reduce((sum, s) => sum + (s.coverage_pct ?? 0), 0) / withCoverage.length;
  })();

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
      role="region"
      aria-label="Watchdog Test Health"
    >
      {/* Header */}
      <div className="px-5 py-3.5 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        <div className="flex items-center gap-2.5">
          <span className="text-base">🛡️</span>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Test Health</h2>
          {!loading && <StatusBadge status={displayStatus} />}
        </div>
        <button
          onClick={triggerRun}
          disabled={triggering || health?.is_running === true}
          className="text-xs px-3 py-1.5 rounded-lg transition-all duration-200 active:scale-95 font-medium"
          style={{
            background: (triggering || health?.is_running) ? 'var(--bg-elevated)' : 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
            color: (triggering || health?.is_running) ? 'var(--text-muted)' : 'white',
            boxShadow: (triggering || health?.is_running) ? 'none' : '0 2px 8px var(--glow-blue)',
          }}
          aria-label="Run tests now"
        >
          {triggering ? (
            <span className="flex items-center gap-1.5">
              <svg className="animate-spin w-3 h-3" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" opacity="0.3" />
                <path d="M14 8a6 6 0 00-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              Starting…
            </span>
          ) : health?.is_running ? (
            <span className="flex items-center gap-1.5">
              <svg className="animate-spin w-3 h-3" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" opacity="0.3" />
                <path d="M14 8a6 6 0 00-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              Running…
            </span>
          ) : (
            'Run Tests Now'
          )}
        </button>
      </div>

      {/* Body */}
      <div className="px-5 py-4">
        {loading ? (
          <div className="space-y-3">
            <div className="h-4 rounded-lg animate-pulse" style={{ background: 'var(--bg-elevated)', width: '60%' }} />
            <div className="h-3 rounded-lg animate-pulse" style={{ background: 'var(--bg-elevated)', width: '40%' }} />
            <div className="h-2 rounded-full animate-pulse" style={{ background: 'var(--bg-elevated)' }} />
          </div>
        ) : error ? (
          <div className="text-center py-4">
            <div className="text-2xl mb-2">⚠️</div>
            <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>{error}</div>
            <button
              onClick={fetchHealth}
              className="text-xs mt-2 px-3 py-1.5 rounded-lg transition-colors"
              style={{ background: 'var(--bg-elevated)', color: 'var(--accent-blue)', border: '1px solid var(--border-subtle)' }}
              aria-label="Retry loading test health"
            >
              Retry
            </button>
          </div>
        ) : !lastRun ? (
          <div className="text-center py-4">
            <div className="text-2xl mb-2">🧪</div>
            <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No test runs yet</div>
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              Click "Run Tests Now" to start the first run
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Summary row */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-muted)' }}>Last Run</div>
                <div className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                  {formatTimestamp(lastRun.started_at)}
                </div>
              </div>
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-muted)' }}>Duration</div>
                <div className="text-sm font-medium" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                  {formatDuration(lastRun.duration_seconds)}
                </div>
              </div>
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-muted)' }}>Tests</div>
                <div className="text-sm font-medium flex items-center gap-1.5">
                  <span style={{ color: 'var(--accent-green)' }}>{totalPassed}</span>
                  <span style={{ color: 'var(--text-muted)' }}>/</span>
                  <span style={{ color: totalFailed > 0 ? 'var(--accent-red)' : 'var(--text-muted)' }}>{totalFailed}</span>
                  <span style={{ color: 'var(--text-muted)' }}>/</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{totalTests}</span>
                </div>
              </div>
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-muted)' }}>Trigger</div>
                <div className="text-xs px-1.5 py-0.5 rounded-md" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                  {lastRun.trigger}
                </div>
              </div>
            </div>

            {/* Average coverage */}
            {avgCoverage !== null && (
              <div>
                <div className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-muted)' }}>Coverage</div>
                <CoverageBar pct={avgCoverage} />
              </div>
            )}

            {/* Suite details */}
            <div className="space-y-2">
              <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Suites</div>
              {lastRun.suites.map(suite => (
                <SuiteCard key={suite.suite} suite={suite} />
              ))}
            </div>

            {/* Summary text */}
            {lastRun.summary && (
              <div className="text-xs p-2.5 rounded-lg" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                {lastRun.summary}
              </div>
            )}

            {/* Scheduler info */}
            {health && (
              <div className="flex items-center gap-2 text-[11px] pt-1" style={{ color: 'var(--text-muted)' }}>
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ background: health.scheduler_active ? 'var(--accent-green)' : 'var(--text-muted)' }}
                />
                Scheduler {health.scheduler_active ? 'active' : 'inactive'}
                {health.scheduler_active && (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    · every {Math.round(health.scheduler_interval_seconds / 60)}m
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Export health status hook for SidebarHealthBadge ─── */

export function useWatchdogHealth(): { status: HealthStatus; loading: boolean } {
  const [status, setStatus] = useState<HealthStatus>('unknown');
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/health/tests', { headers: getAuthHeaders() });
      if (!res.ok) throw new Error();
      const data: TestHealthResponse = await res.json();
      setStatus(data.is_running ? 'running' : (data.status as HealthStatus) || 'unknown');
    } catch {
      setStatus('unknown');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    // Poll every 60s as backup
    const interval = setInterval(fetchStatus, 60_000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  // Listen for WebSocket updates
  const handleWSEvent = useCallback((event: WSEvent) => {
    if ((event as unknown as Record<string, unknown>).type === 'watchdog_report') {
      fetchStatus();
    }
  }, [fetchStatus]);

  useWSSubscribe(handleWSEvent);

  return { status, loading };
}
