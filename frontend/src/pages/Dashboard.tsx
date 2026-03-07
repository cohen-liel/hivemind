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
        return {
          dot: 'bg-green-500',
          pulse: true,
          border: 'border-green-500/20 hover:border-green-500/40',
          glow: 'shadow-[0_0_20px_rgba(34,197,94,0.1)]',
          label: 'Running',
          labelColor: 'text-green-400',
        };
      case 'paused':
        return {
          dot: 'bg-yellow-500',
          pulse: false,
          border: 'border-yellow-500/20 hover:border-yellow-500/30',
          glow: '',
          label: 'Paused',
          labelColor: 'text-yellow-400',
        };
      case 'stopped':
        return {
          dot: 'bg-red-500',
          pulse: false,
          border: 'border-gray-800 hover:border-gray-700',
          glow: '',
          label: 'Stopped',
          labelColor: 'text-red-400',
        };
      default:
        return {
          dot: 'bg-gray-600',
          pulse: false,
          border: 'border-gray-800/60 hover:border-gray-700',
          glow: '',
          label: 'Idle',
          labelColor: 'text-gray-500',
        };
    }
  };

  return (
    <div className="min-h-screen bg-gray-950">
      {/* Header */}
      <header className="border-b border-gray-800/50 bg-gray-900/50 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-white">Mission Control</h1>
            <p className="text-xs text-gray-600 mt-0.5">Manage your agent orchestra</p>
          </div>
          <div className="flex items-center gap-3">
            {'Notification' in window && Notification.permission === 'default' && (
              <button
                onClick={() => Notification.requestPermission()}
                className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1 bg-gray-900 border border-gray-800 rounded-lg transition-colors"
                title="Enable browser notifications"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="inline mr-1">
                  <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/>
                </svg>
                Notify
              </button>
            )}
            <button
              onClick={() => navigate('/new')}
              className="lg:hidden w-8 h-8 rounded-lg bg-blue-600 hover:bg-blue-500 text-white flex items-center justify-center transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
            </button>
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'} ${connected ? 'animate-pulse' : ''}`} />
            <span className="text-[11px] text-gray-600">{connected ? 'Live' : 'Offline'}</span>
          </div>
        </div>
      </header>

      {/* Search + filter bar */}
      {projects.length > 0 && (
        <div className="max-w-5xl mx-auto px-4 sm:px-6 pt-4 flex flex-col sm:flex-row gap-3">
          {/* Search */}
          <div className="relative flex-1">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"/>
              <path d="m21 21-4.35-4.35" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search projects..."
              className="w-full bg-gray-900 border border-gray-800 text-gray-200 text-sm rounded-lg pl-10 pr-4 py-2
                         focus:border-blue-500/50 focus:outline-none placeholder-gray-600"
            />
          </div>
          {/* Status filter pills */}
          <div className="flex items-center gap-1.5">
            {(['all', 'running', 'idle', 'paused', 'stopped'] as const).map(st => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors
                  ${statusFilter === st
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-900 text-gray-500 hover:text-gray-300 border border-gray-800'}`}
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
          <div className="text-center py-20">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-800 flex items-center justify-center text-2xl">
              {'\u{1F3B5}'}
            </div>
            <p className="text-gray-400 text-lg mb-2">No projects yet</p>
            <p className="text-gray-600 text-sm mb-4">Create your first project to start the concert</p>
            <button
              onClick={() => navigate('/new')}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
            >
              Create Project
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredProjects.map(project => {
              const cfg = statusConfig(project.status);
              const live = liveStates[project.project_id];
              const subAgents = project.agents.filter(a => a !== 'orchestrator');

              return (
                <button
                  key={project.project_id}
                  onClick={() => navigate(`/project/${project.project_id}`)}
                  className={`bg-gray-900/80 border rounded-2xl p-5 text-left transition-all duration-300 group
                    ${cfg.border} ${cfg.glow}`}
                >
                  {/* Header: name + status */}
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-base font-bold text-white group-hover:text-blue-400 transition-colors truncate">
                      {project.project_name}
                    </h3>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span className={`w-2 h-2 rounded-full ${cfg.dot} ${cfg.pulse ? 'animate-pulse' : ''}`} />
                      <span className={`text-[11px] font-medium ${cfg.labelColor}`}>{cfg.label}</span>
                    </div>
                  </div>

                  {/* Agent avatars row */}
                  {subAgents.length > 0 && (
                    <div className="flex items-center gap-1.5 mb-3">
                      {subAgents.map(name => {
                        const icon = AGENT_ICONS[name] || '\u{1F527}';
                        const isActive = live?.activeAgents?.has(name);
                        return (
                          <div
                            key={name}
                            className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm transition-all duration-300
                              ${isActive
                                ? 'bg-blue-500/20 shadow-[0_0_12px_rgba(59,130,246,0.4)] scale-110'
                                : 'bg-gray-800/50 opacity-50'}`}
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
                    <div className="text-xs text-blue-300/80 bg-blue-500/10 rounded-lg px-3 py-2 mb-3 truncate font-mono">
                      <span className="text-blue-400/60 mr-1">{live.agent || ''}:</span>
                      {live.text}
                    </div>
                  )}

                  {/* Stats row */}
                  <div className="flex items-center gap-3 text-[11px] text-gray-600">
                    {project.total_cost_usd > 0 && (
                      <span className="font-mono">${project.total_cost_usd.toFixed(3)}</span>
                    )}
                    {project.turn_count > 0 && (
                      <span>{project.turn_count} turns</span>
                    )}
                    {project.agents.length > 0 && (
                      <span>{project.agents.length} agents</span>
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
