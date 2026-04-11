import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { getProjects } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { useTheme } from '../ThemeContext';
import type { Project, WSEvent } from '../types';

const STATUS_CONFIG: Record<string, { color: string; label: string; pulse: boolean }> = {
  running:   { color: 'var(--accent-green)', label: 'Running', pulse: true },
  completed: { color: 'var(--accent-green)', label: 'Completed', pulse: false },
  paused:    { color: 'var(--accent-amber)', label: 'Paused', pulse: false },
  stopped:   { color: 'var(--accent-red)', label: 'Stopped', pulse: false },
  idle:      { color: 'var(--text-muted)', label: 'Idle', pulse: false },
};

interface Props {
  onProjectsChange?: (projects: Project[]) => void;
}

export default function Sidebar({ onProjectsChange }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggleTheme } = useTheme();

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
    const interval = setInterval(loadProjects, 30_000);
    return () => clearInterval(interval);
  }, [loadProjects]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (event.type === 'project_status' && (event as any).status === 'deleted') {
      // Remove locally without re-fetching — API may still return it briefly
      setProjects(prev => prev.filter(p => p.project_id !== event.project_id));
      return;
    }
    if (event.type === 'agent_final' || event.type === 'project_status') {
      loadProjects();
    }
  }, [loadProjects]);

  const { connected } = useWSSubscribe(handleWSEvent);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMeta = e.metaKey || e.ctrlKey;
      if (!isMeta) return;
      // Don't intercept if user is typing in an input
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      switch (e.key) {
        case 'n':
        case 'N':
          e.preventDefault();
          navigate('/new');
          break;
        case '1':
          e.preventDefault();
          navigate('/');
          break;
        case '2':
          e.preventDefault();
          navigate('/schedules');
          break;
        case ',':
          e.preventDefault();
          navigate('/settings');
          break;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [navigate]);

  const currentProjectId = location.pathname.startsWith('/project/')
    ? location.pathname.split('/project/')[1]
    : null;

  // Check if any project is running (for logo breathing effect)
  const hasRunningProject = projects.some(p => p.status === 'running');

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
    {
      path: '/plugins',
      label: 'Plugins',
      icon: (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <path d="M9 11.5h5M11.5 9v5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
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
      role="navigation"
      aria-label="Main navigation"
    >
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 h-14 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border-dim)' }}>
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${hasRunningProject ? 'logo-breathing' : ''}`}
              style={{
                boxShadow: '0 0 12px var(--glow-blue)',
              }}>
              <img src="/favicon-32x32.png" alt="Hivemind" width="28" height="28" style={{ borderRadius: '6px' }} />
            </div>
            <span className="text-sm font-bold truncate"
              style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
              Hivemind
            </span>
            {/* Connection indicator in sidebar */}
            <div className="flex items-center gap-1 ml-auto mr-1" title={connected ? 'Connected' : 'Disconnected'}>
              <span
                className={`w-1.5 h-1.5 rounded-full transition-colors duration-300 ${connected ? 'animate-pulse' : ''}`}
                style={{ background: connected ? 'var(--accent-green)' : 'var(--accent-red)' }}
              />
            </div>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="ml-auto p-1.5 transition-all duration-200 rounded-lg active:scale-90"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
          data-tooltip={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
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

      {/* New Project — premium CTA */}
      <div className="px-3 py-3 flex-shrink-0">
        <button
          onClick={() => navigate('/new')}
          className={`w-full flex items-center gap-2 px-3 py-2.5 text-sm font-semibold rounded-xl transition-all duration-200 active:scale-[0.97]
            ${collapsed ? 'justify-center' : ''}`}
          style={{
            background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
            color: 'white',
            boxShadow: '0 3px 12px rgba(99,140,255,0.3), inset 0 1px 0 rgba(255,255,255,0.12)',
          }}
          onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 5px 20px rgba(99,140,255,0.4), inset 0 1px 0 rgba(255,255,255,0.12)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
          onMouseLeave={e => { e.currentTarget.style.boxShadow = '0 3px 12px rgba(99,140,255,0.3), inset 0 1px 0 rgba(255,255,255,0.12)'; e.currentTarget.style.transform = 'translateY(0)'; }}
          aria-label="New Project"
          {...(collapsed ? { 'data-tooltip': 'New Project' } : {})}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>New Project</span>}
          {!collapsed && <kbd className="ml-auto text-[9px] px-1 py-0.5 rounded" style={{ background: 'rgba(255,255,255,0.1)', color: 'rgba(255,255,255,0.5)' }}>⌘N</kbd>}
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
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium rounded-xl transition-all duration-200 active:scale-[0.98]
                ${collapsed ? 'justify-center' : ''}`}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                borderLeft: isActive ? '2px solid var(--accent-blue)' : '2px solid transparent',
                boxShadow: isActive ? 'inset 0 0 0 1px var(--border-subtle)' : 'none',
              }}
              onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)'; } }}
              onMouseLeave={e => { if (!isActive) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)'; } }}
              aria-label={item.label}
              {...(collapsed ? { 'data-tooltip': item.label } : {})}
            >
              {item.icon}
              {!collapsed && <span>{item.label}</span>}
              {!collapsed && (
                <kbd className="ml-auto text-[9px] px-1 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
                  {item.path === '/' ? '⌘ 1' : item.path === '/schedules' ? '⌘ 2' : '⌘ 3'}
                </kbd>
              )}
            </button>
          );
        })}
      </nav>

      {/* Projects label */}
      {!collapsed && (
        <div className="px-5 py-2 flex-shrink-0 flex items-center justify-between">
          <span className="text-[10px] font-bold tracking-[0.12em] uppercase"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Projects
          </span>
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-md"
            style={{ color: 'var(--text-muted)', background: 'var(--bg-elevated)' }}>
            {projects.length}
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
              className={`w-full flex items-center gap-2.5 px-3 py-2.5 text-[13px] rounded-xl transition-all duration-200 text-left active:scale-[0.98]
                ${collapsed ? 'justify-center' : ''}`}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                boxShadow: isActive ? `inset 0 0 0 1px var(--border-subtle), 0 0 8px ${status.color === 'var(--accent-green)' ? 'var(--glow-green)' : 'transparent'}` : 'none',
              }}
              onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'var(--bg-card)'; } }}
              onMouseLeave={e => { if (!isActive) { e.currentTarget.style.background = 'transparent'; } }}
              title={`${project.project_name} (${status.label})`}
            >
              <span
                className={`w-2.5 h-2.5 rounded-full flex-shrink-0 transition-all duration-300 ${status.pulse ? 'animate-pulse' : ''}`}
                style={{
                  backgroundColor: status.color,
                  boxShadow: status.pulse ? `0 0 8px ${status.color}` : 'none',
                }}
              />
              {!collapsed && (
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate block font-medium flex-1">{project.project_name}</span>
                  </div>
                </div>
              )}
            </button>
          );
        })}
        {projects.length === 0 && !collapsed && (
          <div className="text-xs px-3 py-6 text-center" style={{ color: 'var(--text-muted)' }}>
            <div className="text-2xl mb-2">📂</div>
            No projects yet
          </div>
        )}
      </div>

      {/* Theme toggle + Settings */}
      <div className="px-2 py-3 flex-shrink-0 space-y-0.5" style={{ borderTop: '1px solid var(--border-dim)' }}>
        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className={`theme-toggle w-full flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium rounded-xl transition-all duration-200 active:scale-[0.98]
            ${collapsed ? 'justify-center' : ''}`}
          style={{
            background: 'transparent',
            color: 'var(--text-secondary)',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-card)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          {...(collapsed ? { 'data-tooltip': theme === 'dark' ? 'Light Mode' : 'Dark Mode' } : {})}
        >
          {theme === 'dark' ? (
            /* Sun icon — shown in dark mode, click to go light */
            <svg className="theme-toggle-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.3"/>
              <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"
                stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            </svg>
          ) : (
            /* Moon icon — shown in light mode, click to go dark */
            <svg className="theme-toggle-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M13.5 9.5a5.5 5.5 0 01-7-7 5.5 5.5 0 107 7z"
                stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
          {!collapsed && <span>{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>}
        </button>

        {/* Settings */}
        <button
          onClick={() => navigate('/settings')}
          className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium rounded-xl transition-all duration-200 active:scale-[0.98]
            ${collapsed ? 'justify-center' : ''}`}
          style={{
            background: location.pathname === '/settings' ? 'var(--bg-elevated)' : 'transparent',
            color: location.pathname === '/settings' ? 'var(--text-primary)' : 'var(--text-secondary)',
          }}
          onMouseEnter={e => { if (location.pathname !== '/settings') { e.currentTarget.style.background = 'var(--bg-card)'; } }}
          onMouseLeave={e => { if (location.pathname !== '/settings') { e.currentTarget.style.background = 'transparent'; } }}
          aria-label="Settings"
          {...(collapsed ? { 'data-tooltip': 'Settings' } : {})}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"
              stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>Settings</span>}
          {!collapsed && <kbd className="ml-auto text-[9px] px-1 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>⌘ ,</kbd>}
        </button>
      </div>
    </aside>
  );
}
