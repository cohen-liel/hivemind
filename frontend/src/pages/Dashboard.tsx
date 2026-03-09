import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { getProjects } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { AGENT_ICONS } from '../constants';
import { DashboardSkeleton } from '../components/Skeleton';
import ErrorState from '../components/ErrorState';
import CostChart from '../components/CostChart';
import { useAnimatedNumber, formatCost } from '../hooks/useAnimatedNumber';
import { usePageTitle } from '../hooks/usePageTitle';
import type { Project, WSEvent } from '../types';

/** Format a Unix timestamp into a human-readable relative time string. */
function formatRelativeTime(timestamp: number | undefined): string {
  if (!timestamp) return '';
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - timestamp);

  if (diff < 60) return 'just now';
  if (diff < 3600) {
    const mins = Math.floor(diff / 60);
    return `${mins}m ago`;
  }
  if (diff < 86400) {
    const hrs = Math.floor(diff / 3600);
    return `${hrs}h ago`;
  }
  if (diff < 604800) {
    const days = Math.floor(diff / 86400);
    return `${days}d ago`;
  }
  const weeks = Math.floor(diff / 604800);
  return `${weeks}w ago`;
}

interface DashboardLiveState {
  text: string;
  agent?: string;
  activeAgents: Set<string>;
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [liveStates, setLiveStates] = useState<Record<string, DashboardLiveState>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [costExpanded, setCostExpanded] = useState(false);

  // Dynamic page title
  usePageTitle('Dashboard');

  const loadData = useCallback(async () => {
    try {
      const p = await getProjects();
      setProjects(p);
      setError(null);
    } catch (e: unknown) {
      if (projects.length === 0) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      }
    } finally {
      setLoading(false);
    }
  }, [projects.length]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (!event.project_id) return;
    const pid = event.project_id;

    if (event.type === 'tool_use' && event.description) {
      setLiveStates(prev => ({
        ...prev,
        [pid]: {
          ...prev[pid],
          text: event.description!,
          agent: event.agent,
          activeAgents: prev[pid]?.activeAgents ?? new Set(),
        },
      }));
    } else if (event.type === 'agent_started' && event.agent) {
      // Immediately mark project as running (don't wait for API poll)
      setProjects(prev => prev.map(p =>
        p.project_id === pid ? { ...p, status: 'running' } : p
      ));
      setLiveStates(prev => {
        const existing = prev[pid] ?? { text: '', activeAgents: new Set() };
        const active = new Set(existing.activeAgents);
        active.add(event.agent!);
        return {
          ...prev,
          [pid]: {
            text: event.task ? `${event.agent}: ${event.task.slice(0, 80)}` : `${event.agent} started`,
            agent: event.agent,
            activeAgents: active,
          },
        };
      });
    } else if (event.type === 'agent_finished' && event.agent) {
      setLiveStates(prev => {
        const existing = prev[pid] ?? { text: '', activeAgents: new Set() };
        const active = new Set(existing.activeAgents);
        active.delete(event.agent!);
        return {
          ...prev,
          [pid]: {
            text: `${event.agent} ${event.is_error ? 'failed' : 'done'}`,
            agent: event.agent,
            activeAgents: active,
          },
        };
      });
    } else if (event.type === 'project_status' && event.status) {
      // Immediately reflect status change (running/idle/paused) on the card
      setProjects(prev => prev.map(p =>
        p.project_id === pid ? { ...p, status: event.status as Project['status'] } : p
      ));
      // Reload full project data (costs, turn counts etc.)
      loadData();
    } else if (event.type === 'agent_final') {
      // Task fully done — reload and clear live state
      loadData();
      setLiveStates(prev => {
        const next = { ...prev };
        delete next[pid];
        return next;
      });
    }
  }, [loadData]);

  const { connected } = useWSSubscribe(handleWSEvent);

  // Filter projects
  const filteredProjects = projects.filter(p => {
    if (statusFilter !== 'all' && p.status !== statusFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return p.project_name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q);
    }
    return true;
  });

  const statusConfig = (status: string): {
    color: string;
    bg: string;
    glow: string;
    pulse: boolean;
    label: string;
    cardClass: string;
  } => {
    switch (status) {
      case 'running':
        return {
          color: 'var(--status-running-text)',
          bg: 'var(--status-running-bg)',
          glow: 'var(--glow-green)',
          pulse: true,
          label: 'Running',
          cardClass: 'card-running',
        };
      case 'paused':
        return {
          color: 'var(--status-paused-text)',
          bg: 'var(--status-paused-bg)',
          glow: 'rgba(245,166,35,0.08)',
          pulse: false,
          label: 'Paused',
          cardClass: '',
        };
      case 'stopped':
        return {
          color: 'var(--status-stopped-text)',
          bg: 'var(--status-stopped-bg)',
          glow: 'var(--glow-red)',
          pulse: false,
          label: 'Stopped',
          cardClass: '',
        };
      default:
        return {
          color: 'var(--status-idle-text)',
          bg: 'var(--status-idle-bg)',
          glow: 'transparent',
          pulse: false,
          label: 'Idle',
          cardClass: '',
        };
    }
  };

  const runningCount = projects.filter(p => p.status === 'running').length;
  const totalCost = projects.reduce((sum, p) => sum + (p.total_cost_usd || 0), 0);

  // Animated stat values
  const animatedCost = useAnimatedNumber(totalCost, 700, totalCost < 1 ? 3 : 2);

  // Loading state
  if (loading && projects.length === 0) {
    return <DashboardSkeleton />;
  }

  // Error state (only when no projects loaded yet)
  if (error && projects.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
        <ErrorState
          variant="connection"
          onRetry={() => { setLoading(true); loadData(); }}
        />
      </div>
    );
  }

  return (
    <div className="min-h-screen safe-area-top page-enter" style={{ background: 'var(--bg-void)' }}>
      {/* Hero Header */}
      <header className="relative overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        {/* Gradient mesh background */}
        <div className="absolute inset-0" style={{
          background: 'radial-gradient(ellipse at 20% 50%, rgba(99,140,255,0.08) 0%, transparent 50%), radial-gradient(ellipse at 80% 20%, rgba(167,139,250,0.06) 0%, transparent 50%)',
        }} />
        <div className="relative max-w-5xl mx-auto px-4 sm:px-6 py-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3 mb-1">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl"
                  style={{ background: 'var(--glow-blue)', boxShadow: '0 0 20px var(--glow-blue)' }}>
                  ⚡
                </div>
                <div>
                  <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                    Nexus
                  </h1>
                  <p className="text-xs" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    Agent OS
                  </p>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-4">
              {/* Live stats */}
              <div className="hidden sm:flex items-center gap-4">
                {runningCount > 0 && (
                  <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full"
                    style={{ background: 'var(--glow-green)', border: '1px solid rgba(61,214,140,0.15)' }}>
                    <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
                    <span className="text-xs font-medium" style={{ color: 'var(--accent-green)' }}>
                      {runningCount} active
                    </span>
                  </div>
                )}
                {totalCost > 0 && (
                  <span className="telemetry" style={{ color: 'var(--text-muted)' }}>
                    Total: ${animatedCost}
                  </span>
                )}
              </div>

              {'Notification' in window && Notification.permission === 'default' && (
                <button
                  onClick={() => Notification.requestPermission()}
                  className="text-xs px-3 py-1.5 rounded-lg transition-all"
                  style={{ color: 'var(--text-muted)', border: '1px solid var(--border-subtle)' }}
                  aria-label="Enable browser notifications"
                >
                  🔔 Notify
                </button>
              )}

              {/* Connection status */}
              <div className="flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${connected ? 'animate-pulse' : ''}`}
                  style={{ background: connected ? 'var(--accent-green)' : 'var(--accent-red)' }} />
                <span className="text-[10px]"
                  style={{ color: connected ? 'var(--accent-green)' : 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
                  {connected ? 'LIVE' : 'RECONNECTING...'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Search + filter bar */}
      {projects.length > 0 && (
        <div className="max-w-5xl mx-auto px-4 sm:px-6 pt-5 flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search projects..."
              aria-label="Search projects"
              className="w-full text-sm rounded-xl pl-10 pr-4 py-2.5 focus:outline-none transition-colors"
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
              }}
            />
          </div>
          <div className="flex items-center gap-1.5 overflow-x-auto" role="group" aria-label="Filter projects by status">
            {(['all', 'running', 'idle', 'paused'] as const).map(st => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                aria-pressed={statusFilter === st}
                aria-label={`Filter: ${st === 'all' ? 'All statuses' : st}`}
                className="px-3 py-2 rounded-xl text-xs font-medium transition-all whitespace-nowrap"
                style={{
                  background: statusFilter === st ? 'var(--accent-blue)' : 'var(--bg-panel)',
                  color: statusFilter === st ? 'white' : 'var(--text-muted)',
                  border: statusFilter === st ? '1px solid var(--accent-blue)' : '1px solid var(--border-dim)',
                  boxShadow: statusFilter === st ? '0 2px 10px var(--glow-blue)' : 'none',
                }}
              >
                {st === 'all' ? 'All' : st.charAt(0).toUpperCase() + st.slice(1)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Project cards */}
      <main className="max-w-5xl mx-auto px-3 sm:px-6 py-4 sm:py-6">
        {projects.length === 0 ? (
          /* ── Welcome empty state ── */
          <div className="flex items-center justify-center py-12 sm:py-16">
            <div
              className="relative max-w-sm w-full rounded-2xl p-6 sm:p-8 text-center glass-panel"
              style={{
                boxShadow: '0 0 80px rgba(99, 140, 255, 0.06), 0 25px 50px rgba(0,0,0,0.3)',
              }}
              role="region"
              aria-label="Welcome to Nexus — no projects yet"
            >
              {/* Subtle glow behind the card */}
              <div className="absolute inset-0 -z-10 rounded-2xl"
                style={{
                  background: 'radial-gradient(ellipse at 50% 0%, rgba(99,140,255,0.08) 0%, transparent 70%)',
                  filter: 'blur(20px)',
                }} />

              {/* Network constellation SVG illustration */}
              <svg
                width="160"
                height="120"
                viewBox="0 0 160 120"
                fill="none"
                className="mx-auto mb-5"
                aria-hidden="true"
              >
                {/* Connection lines (animated dashes) */}
                <line x1="80" y1="60" x2="30" y2="28" stroke="var(--accent-blue)" strokeWidth="1" opacity="0.2" strokeDasharray="4 4" className="empty-state-line" />
                <line x1="80" y1="60" x2="130" y2="25" stroke="var(--accent-purple)" strokeWidth="1" opacity="0.2" strokeDasharray="4 4" className="empty-state-line" />
                <line x1="80" y1="60" x2="125" y2="95" stroke="var(--accent-green)" strokeWidth="1" opacity="0.2" strokeDasharray="4 4" className="empty-state-line" />
                <line x1="80" y1="60" x2="35" y2="92" stroke="var(--accent-cyan)" strokeWidth="1" opacity="0.15" strokeDasharray="4 4" className="empty-state-line" />
                {/* Central hub node */}
                <circle cx="80" cy="60" r="16" fill="var(--glow-blue)" />
                <circle cx="80" cy="60" r="16" stroke="var(--accent-blue)" strokeWidth="1.5" fill="none" opacity="0.4" />
                {/* Lightning bolt icon */}
                <path d="M83 53L77 61H83L77 69" stroke="var(--accent-blue)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                {/* Satellite agent nodes */}
                <circle cx="30" cy="28" r="8" fill="var(--glow-blue)" />
                <circle cx="30" cy="28" r="8" stroke="var(--accent-blue)" strokeWidth="1" fill="none" opacity="0.3" />
                <circle cx="130" cy="25" r="7" fill="var(--glow-blue)" />
                <circle cx="130" cy="25" r="7" stroke="var(--accent-purple)" strokeWidth="1" fill="none" opacity="0.3" />
                <circle cx="125" cy="95" r="9" fill="var(--glow-green)" />
                <circle cx="125" cy="95" r="9" stroke="var(--accent-green)" strokeWidth="1" fill="none" opacity="0.3" />
                <circle cx="35" cy="92" r="6" fill="var(--glow-blue)" />
                <circle cx="35" cy="92" r="6" stroke="var(--accent-cyan)" strokeWidth="1" fill="none" opacity="0.2" />
                {/* Tiny agent emojis inside nodes */}
                <text x="30" y="31" textAnchor="middle" fontSize="8">🎨</text>
                <text x="130" y="28" textAnchor="middle" fontSize="7">⚡</text>
                <text x="125" y="98" textAnchor="middle" fontSize="8">🔍</text>
                <text x="35" y="95" textAnchor="middle" fontSize="6">🧪</text>
              </svg>

              <h2 className="text-lg font-bold mb-2" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                Welcome to Nexus
              </h2>
              <p className="text-sm mb-6 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                Orchestrate multi-agent AI teams to build, review, and ship code.
                Create your first project to get started.
              </p>

              <button
                onClick={() => navigate('/new')}
                className="px-6 py-3 text-sm font-semibold rounded-xl transition-all duration-200 text-white active:scale-[0.97]"
                style={{
                  background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                  boxShadow: '0 4px 20px var(--glow-blue), inset 0 1px 0 rgba(255,255,255,0.12)',
                }}
                aria-label="Create a new project"
                onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 6px 30px rgba(99,140,255,0.4), inset 0 1px 0 rgba(255,255,255,0.12)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                onMouseLeave={e => { e.currentTarget.style.boxShadow = '0 4px 20px var(--glow-blue), inset 0 1px 0 rgba(255,255,255,0.12)'; e.currentTarget.style.transform = 'translateY(0)'; }}
              >
                <span className="flex items-center gap-2">
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                  </svg>
                  New Project
                </span>
              </button>

              <p className="text-[10px] mt-4" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                ⌘N to quick-create &bull; ? for shortcuts
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredProjects.map((project, i) => {
              const cfg = statusConfig(project.status);
              const live = liveStates[project.project_id];
              const subAgents = project.agents.filter(a => a !== 'orchestrator');
              const lastActivity = formatRelativeTime(
                project.updated_at || project.last_message?.timestamp || project.created_at
              );

              return (
                <button
                  key={project.project_id}
                  onClick={() => navigate(`/project/${project.project_id}`)}
                  aria-label={`Open project ${project.project_name}, status: ${cfg.label}, ${subAgents.length} agents`}
                  className={`text-left transition-all duration-300 group rounded-2xl p-4 sm:p-5 card-hover ${cfg.cardClass}`}
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-dim)',
                    animation: `slideUp 0.3s ease-out ${i * 60}ms backwards`,
                  }}
                >
                  {/* Header: name + status badge */}
                  <div className="flex items-center justify-between mb-3 gap-2">
                    <h3 className="text-sm sm:text-base font-bold truncate min-w-0 transition-colors"
                      style={{ color: 'var(--text-primary)' }}>
                      {project.project_name}
                    </h3>
                    <div
                      className="flex items-center gap-1.5 flex-shrink-0 px-2.5 py-1 rounded-full status-badge-pop"
                      style={{ background: cfg.bg }}
                      role="status"
                      aria-label={`Status: ${cfg.label}`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${cfg.pulse ? 'animate-pulse' : ''}`}
                        style={{ background: cfg.color }}
                      />
                      <span className="text-[10px] font-bold tracking-wider"
                        style={{ color: cfg.color, fontFamily: 'var(--font-mono)' }}>
                        {cfg.label.toUpperCase()}
                      </span>
                    </div>
                  </div>

                  {/* Description */}
                  {project.description && (
                    <p className="text-xs mb-3 leading-relaxed line-clamp-2" style={{ color: 'var(--text-muted)' }}>
                      {project.description.slice(0, 120)}
                    </p>
                  )}

                  {/* Agent avatars row */}
                  {subAgents.length > 0 && (
                    <div className="flex items-center gap-1.5 sm:gap-2 mb-3 flex-wrap">
                      {subAgents.map(name => {
                        const icon = AGENT_ICONS[name] || '🔧';
                        const isActive = live?.activeAgents?.has(name);
                        return (
                          <div
                            key={name}
                            className="w-8 h-8 rounded-lg flex items-center justify-center text-sm transition-all duration-500"
                            style={{
                              background: isActive ? 'var(--glow-blue)' : 'var(--bg-elevated)',
                              border: isActive ? '1px solid rgba(99,140,255,0.3)' : '1px solid var(--border-dim)',
                              boxShadow: isActive ? '0 0 12px var(--glow-blue)' : 'none',
                              opacity: isActive ? 1 : 0.5,
                              transform: isActive ? 'scale(1.1)' : 'scale(1)',
                            }}
                            title={name}
                            aria-label={`${name}${isActive ? ' (active)' : ''}`}
                          >
                            {icon}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* Live activity text */}
                  {live?.text && (
                    <div className="text-xs rounded-lg px-3 py-2 mb-3 truncate"
                      style={{
                        background: 'var(--glow-blue)',
                        color: 'var(--accent-blue)',
                        fontFamily: 'var(--font-mono)',
                        border: '1px solid rgba(99,140,255,0.1)',
                      }}
                      role="log"
                      aria-live="polite"
                    >
                      <span style={{ opacity: 0.6 }}>{live.agent || ''}:</span> {live.text}
                    </div>
                  )}

                  {/* Stats row */}
                  <div className="flex items-center gap-2 sm:gap-3 flex-wrap pt-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
                    {project.total_cost_usd > 0 && (
                      <span className="telemetry stat-item" style={{ color: 'var(--accent-green)' }}>
                        {formatCost(project.total_cost_usd)}
                      </span>
                    )}
                    {project.turn_count > 0 && (
                      <span className="telemetry stat-item">{project.turn_count} turns</span>
                    )}
                    <span className="telemetry stat-item">
                      {subAgents.length} {subAgents.length === 1 ? 'agent' : 'agents'}
                    </span>
                    {lastActivity && (
                      <span className="telemetry stat-item ml-auto" style={{ color: 'var(--text-muted)' }}>
                        {lastActivity}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* ── Cost Analytics (collapsible) ── */}
        {projects.length > 0 && (
          <div
            className="mt-6 rounded-2xl overflow-hidden transition-all duration-300 glass-panel"
          >
            <button
              onClick={() => setCostExpanded(prev => !prev)}
              aria-expanded={costExpanded}
              aria-label="Toggle cost analytics panel"
              className="w-full flex items-center justify-between px-5 py-4 transition-colors"
              style={{ color: 'var(--text-primary)' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                  style={{ background: 'var(--glow-green)' }}
                >
                  💰
                </div>
                <div className="text-left">
                  <h3 className="text-sm font-bold" style={{ fontFamily: 'var(--font-display)' }}>
                    Cost Analytics
                  </h3>
                  <p className="text-[10px]" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    Last 7 days
                  </p>
                </div>
              </div>
              <svg
                className={`w-4 h-4 transition-transform duration-300 ${costExpanded ? 'rotate-180' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
                style={{ color: 'var(--text-muted)' }}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {costExpanded && (
              <div
                className="px-5 pb-5 animate-[fadeSlideIn_0.25s_ease-out]"
                style={{ borderTop: '1px solid var(--border-dim)' }}
              >
                <CostChart />
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
