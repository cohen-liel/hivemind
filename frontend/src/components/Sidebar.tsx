import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { getProjects } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import type { Project, WSEvent } from '../types';

const STATUS_CONFIG: Record<string, { color: string; label: string; pulse: boolean }> = {
  running: { color: '#3dd68c', label: 'Running', pulse: true },
  paused:  { color: '#f5a623', label: 'Paused', pulse: false },
  stopped: { color: '#f5475b', label: 'Stopped', pulse: false },
  idle:    { color: '#4a4e63', label: 'Idle', pulse: false },
};

interface Props {
  onProjectsChange?: (projects: Project[]) => void;
}

export default function Sidebar({ onProjectsChange }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const loadProjects = useCallback(async () => {
    try {
      const p = await getProjects();
      setProjects(p);
      onProjectsChange?.(p);
    } catch {
      // API not ready
    }
  }, [onProjectsChange]);

  useEffect(() => {
    loadProjects();
    const interval = setInterval(loadProjects, 5000);
    return () => clearInterval(interval);
  }, [loadProjects]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (event.type === 'agent_final' || event.type === 'project_status') {
      loadProjects();
    }
  }, [loadProjects]);

  useWSSubscribe(handleWSEvent);

  const currentProjectId = location.pathname.startsWith('/project/')
    ? location.pathname.split('/project/')[1]
    : null;

  const navItems = [
    {
      path: '/',
      label: 'Dashboard',
      icon: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
        </svg>
      ),
    },
    {
      path: '/schedules',
      label: 'Schedules',
      icon: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3"/>
          <path d="M8 5v3l2 2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      ),
    },
  ];

  return (
    <aside
      className={`flex flex-col flex-shrink-0 transition-all duration-300 ${collapsed ? 'w-[60px]' : 'w-60'}`}
      style={{
        background: 'var(--bg-panel)',
        borderRight: '1px solid var(--border-dim)',
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 h-14 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border-dim)' }}>
        {!collapsed && (
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-6 h-6 rounded-lg flex items-center justify-center text-xs"
              style={{ background: 'var(--glow-blue)', color: 'var(--accent-blue)' }}>
              ⚡
            </div>
            <span className="text-sm font-semibold text-[var(--text-primary)] truncate"
              style={{ fontFamily: 'var(--font-display)' }}>
              Claude Bot
            </span>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="ml-auto p-1 transition-colors rounded-md hover:bg-[var(--bg-elevated)]"
          style={{ color: 'var(--text-muted)' }}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            {collapsed ? (
              <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            ) : (
              <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            )}
          </svg>
        </button>
      </div>

      {/* New Project */}
      <div className="px-3 py-3 flex-shrink-0">
        <button
          onClick={() => navigate('/new')}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg transition-all duration-200
            ${collapsed ? 'justify-center' : ''}`}
          style={{
            background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
            color: 'white',
            boxShadow: '0 2px 8px rgba(99,140,255,0.25)',
          }}
          onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 4px 16px rgba(99,140,255,0.35)')}
          onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 2px 8px rgba(99,140,255,0.25)')}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>New Project</span>}
        </button>
      </div>

      {/* Navigation */}
      <nav className="px-2 mb-2 flex-shrink-0 space-y-0.5">
        {navItems.map(item => {
          const isActive = location.pathname === item.path;
          return (
            <button
              key={item.path}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] rounded-lg transition-all duration-150
                ${collapsed ? 'justify-center' : ''}`}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                borderLeft: isActive ? '2px solid var(--accent-blue)' : '2px solid transparent',
              }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)'; }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
            >
              {item.icon}
              {!collapsed && <span>{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* Projects label */}
      {!collapsed && (
        <div className="px-5 py-1.5 flex-shrink-0">
          <span className="text-[10px] font-bold tracking-[0.12em] uppercase"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Projects
          </span>
        </div>
      )}

      {/* Project list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
        {projects.map(project => {
          const isActive = currentProjectId === project.project_id;
          const status = STATUS_CONFIG[project.status] || STATUS_CONFIG.idle;

          return (
            <button
              key={project.project_id}
              onClick={() => navigate(`/project/${project.project_id}`)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] rounded-lg transition-all duration-150 text-left
                ${collapsed ? 'justify-center' : ''}`}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-card)'; }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = isActive ? 'var(--bg-elevated)' : 'transparent'; }}
              title={`${project.project_name} (${status.label})`}
            >
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${status.pulse ? 'animate-pulse' : ''}`}
                style={{ backgroundColor: status.color }}
              />
              {!collapsed && (
                <span className="truncate">{project.project_name}</span>
              )}
            </button>
          );
        })}
        {projects.length === 0 && !collapsed && (
          <p className="text-xs px-3 py-4 text-center" style={{ color: 'var(--text-muted)' }}>
            No projects yet
          </p>
        )}
      </div>

      {/* Settings */}
      <div className="px-2 py-3 flex-shrink-0" style={{ borderTop: '1px solid var(--border-dim)' }}>
        <button
          onClick={() => navigate('/settings')}
          className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] rounded-lg transition-all duration-150
            ${collapsed ? 'justify-center' : ''}`}
          style={{
            background: location.pathname === '/settings' ? 'var(--bg-elevated)' : 'transparent',
            color: location.pathname === '/settings' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
          onMouseEnter={e => { if (location.pathname !== '/settings') e.currentTarget.style.background = 'var(--bg-card)'; }}
          onMouseLeave={e => { if (location.pathname !== '/settings') e.currentTarget.style.background = 'transparent'; }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"
              stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>Settings</span>}
        </button>
      </div>
    </aside>
  );
}
