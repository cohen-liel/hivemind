import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { getProjects } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { AGENT_ICONS } from '../constants';
import type { Project, WSEvent } from '../types';

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

  const loadData = useCallback(async () => {
    try {
      const p = await getProjects();
      setProjects(p);
    } catch {
      // API not ready yet
    }
  }, []);

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
    } else if (event.type === 'agent_final' || event.type === 'project_status') {
      loadData();
      if (event.type === 'agent_final') {
        setLiveStates(prev => {
          const next = { ...prev };
          delete next[pid];
          return next;
        });
      }
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

  const statusConfig = (status: string) => {
    switch (status) {
      case 'running':
        return { color: 'var(--accent-green)', glow: 'var(--glow-green)', pulse: true, label: 'Running' };
      case 'paused':
        return { color: 'var(--accent-amber)', glow: 'rgba(245,166,35,0.08)', pulse: false, label: 'Paused' };
      case 'stopped':
        return { color: 'var(--accent-red)', glow: 'var(--glow-red)', pulse: false, label: 'Stopped' };
      default:
        return { color: 'var(--text-muted)', glow: 'transparent', pulse: false, label: 'Idle' };
    }
  };

  const runningCount = projects.filter(p => p.status === 'running').length;
  const totalCost = projects.reduce((sum, p) => sum + (p.total_cost_usd || 0), 0);

  return (
    <div className="min-h-screen safe-area-top" style={{ background: 'var(--bg-void)' }}>
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
                    Total: ${totalCost.toFixed(2)}
                  </span>
                )}
              </div>

              {'Notification' in window && Notification.permission === 'default' && (
                <button
                  onClick={() => Notification.requestPermission()}
                  className="text-xs px-3 py-1.5 rounded-lg transition-all"
                  style={{ color: 'var(--text-muted)', border: '1px solid var(--border-subtle)' }}
                  title="Enable browser notifications"
                >
                  🔔 Notify
                </button>
              )}

              {/* Connection status */}
              <div className="flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${connected ? 'animate-pulse' : ''}`}
                  style={{ background: connected ? 'var(--accent-green)' : 'var(--accent-red)' }} />
                <span className="text-[10px]"
                  style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  {connected ? 'LIVE' : 'OFFLINE'}
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
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search projects..."
              className="w-full text-sm rounded-xl pl-10 pr-4 py-2.5 focus:outline-none transition-colors"
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
              }}
            />
          </div>
          <div className="flex items-center gap-1.5">
            {(['all', 'running', 'idle', 'paused'] as const).map(st => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                className="px-3 py-2 rounded-xl text-xs font-medium transition-all"
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
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        {projects.length === 0 ? (
          <div className="text-center py-24">
            <div className="w-20 h-20 mx-auto mb-5 rounded-2xl flex items-center justify-center text-4xl"
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-dim)' }}>
              🚀
            </div>
            <p className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>No projects yet</p>
            <p className="text-sm mb-6" style={{ color: 'var(--text-muted)' }}>Create your first project to start orchestrating agents</p>
            <button
              onClick={() => navigate('/new')}
              className="px-5 py-2.5 text-sm font-medium rounded-xl transition-all text-white"
              style={{
                background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                boxShadow: '0 4px 20px var(--glow-blue)',
              }}
            >
              + Create Project
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredProjects.map((project, i) => {
              const cfg = statusConfig(project.status);
              const live = liveStates[project.project_id];
              const subAgents = project.agents.filter(a => a !== 'orchestrator');

              return (
                <button
                  key={project.project_id}
                  onClick={() => navigate(`/project/${project.project_id}`)}
                  className="text-left transition-all duration-300 group rounded-2xl p-5 card-hover"
                  style={{
                    background: 'var(--bg-card)',
                    border: `1px solid ${project.status === 'running' ? 'rgba(61,214,140,0.2)' : 'var(--border-dim)'}`,
                    boxShadow: project.status === 'running' ? `0 0 30px ${cfg.glow}` : 'none',
                    animation: `slideUp 0.3s ease-out ${i * 60}ms backwards`,
                  }}
                >
                  {/* Header: name + status */}
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-base font-bold truncate transition-colors"
                      style={{ color: 'var(--text-primary)' }}>
                      {project.project_name}
                    </h3>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span
                        className={`w-2 h-2 rounded-full ${cfg.pulse ? 'animate-pulse' : ''}`}
                        style={{ background: cfg.color }}
                      />
                      <span className="text-[11px] font-bold tracking-wider"
                        style={{ color: cfg.color, fontFamily: 'var(--font-mono)' }}>
                        {cfg.label.toUpperCase()}
                      </span>
                    </div>
                  </div>

                  {/* Description */}
                  {project.description && (
                    <p className="text-xs mb-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                      {project.description.slice(0, 100)}
                    </p>
                  )}

                  {/* Agent avatars row */}
                  {subAgents.length > 0 && (
                    <div className="flex items-center gap-2 mb-3">
                      {subAgents.map(name => {
                        const icon = AGENT_ICONS[name] || '🔧';
                        const isActive = live?.activeAgents?.has(name);
                        return (
                          <div
                            key={name}
                            className="w-9 h-9 rounded-xl flex items-center justify-center text-sm transition-all duration-500"
                            style={{
                              background: isActive ? 'var(--glow-blue)' : 'var(--bg-elevated)',
                              border: isActive ? '1px solid rgba(99,140,255,0.3)' : '1px solid var(--border-dim)',
                              boxShadow: isActive ? '0 0 15px var(--glow-blue)' : 'none',
                              opacity: isActive ? 1 : 0.5,
                              transform: isActive ? 'scale(1.1)' : 'scale(1)',
                            }}
                            title={name}
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
                      }}>
                      <span style={{ opacity: 0.6 }}>{live.agent || ''}:</span> {live.text}
                    </div>
                  )}

                  {/* Stats row */}
                  <div className="flex items-center gap-3 pt-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
                    {project.total_cost_usd > 0 && (
                      <span className="telemetry" style={{ color: 'var(--accent-green)' }}>
                        ${project.total_cost_usd.toFixed(3)}
                      </span>
                    )}
                    {project.turn_count > 0 && (
                      <span className="telemetry">{project.turn_count} turns</span>
                    )}
                    {project.agents.length > 0 && (
                      <span className="telemetry">{project.agents.length} agents</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
