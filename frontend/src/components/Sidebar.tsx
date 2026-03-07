import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { getProjects } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import type { Project, WSEvent } from '../types';

interface Props {
  onProjectsChange?: (projects: Project[]) => void;
}

const STATUS_DOT: Record<string, string> = {
  running: 'bg-green-500',
  paused: 'bg-yellow-500',
  stopped: 'bg-red-500',
  idle: 'bg-gray-500',
};

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

  return (
    <aside className={`flex flex-col border-r border-gray-800 bg-gray-900/70 backdrop-blur-sm transition-all duration-200 ${collapsed ? 'w-16' : 'w-64'} flex-shrink-0`}>
      {/* Logo / Header */}
      <div className="flex items-center gap-2 px-4 h-14 border-b border-gray-800 flex-shrink-0">
        {!collapsed && (
          <h1 className="text-sm font-semibold text-white truncate">Claude Bot</h1>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="ml-auto text-gray-500 hover:text-white transition-colors p-1"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            {collapsed ? (
              <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            ) : (
              <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            )}
          </svg>
        </button>
      </div>

      {/* New Project button */}
      <div className="px-3 py-3 flex-shrink-0">
        <button
          onClick={() => navigate('/new')}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg
                     bg-blue-600 hover:bg-blue-500 text-white transition-colors
                     ${collapsed ? 'justify-center' : ''}`}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>New Project</span>}
        </button>
      </div>

      {/* Navigation */}
      <nav className="px-3 mb-2 flex-shrink-0">
        <button
          onClick={() => navigate('/')}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors
                     ${location.pathname === '/'
                       ? 'bg-gray-800 text-white'
                       : 'text-gray-400 hover:text-white hover:bg-gray-800/50'}
                     ${collapsed ? 'justify-center' : ''}`}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8l6-5 6 5M3 7.5V13a1 1 0 001 1h8a1 1 0 001-1V7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          {!collapsed && <span>Dashboard</span>}
        </button>
        <button
          onClick={() => navigate('/schedules')}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors
                     ${location.pathname === '/schedules'
                       ? 'bg-gray-800 text-white'
                       : 'text-gray-400 hover:text-white hover:bg-gray-800/50'}
                     ${collapsed ? 'justify-center' : ''}`}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M8 5v3l2 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          {!collapsed && <span>Schedules</span>}
        </button>
      </nav>

      {/* Projects label */}
      {!collapsed && (
        <div className="px-6 py-1 flex-shrink-0">
          <span className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">Projects</span>
        </div>
      )}

      {/* Project list */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-0.5">
        {projects.map(project => {
          const isActive = currentProjectId === project.project_id;
          const dotColor = STATUS_DOT[project.status] || STATUS_DOT.idle;

          return (
            <button
              key={project.project_id}
              onClick={() => navigate(`/project/${project.project_id}`)}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors text-left
                         ${isActive
                           ? 'bg-gray-800 text-white'
                           : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'}
                         ${collapsed ? 'justify-center' : ''}`}
              title={project.project_name}
            >
              <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor} ${project.status === 'running' ? 'animate-pulse' : ''}`} />
              {!collapsed && (
                <span className="truncate">{project.project_name}</span>
              )}
            </button>
          );
        })}
        {projects.length === 0 && !collapsed && (
          <p className="text-xs text-gray-600 px-3 py-4 text-center">No projects yet</p>
        )}
      </div>

      {/* Settings at bottom */}
      <div className="px-3 py-3 border-t border-gray-800 flex-shrink-0">
        <button
          onClick={() => navigate('/settings')}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors
                     ${location.pathname === '/settings'
                       ? 'bg-gray-800 text-white'
                       : 'text-gray-400 hover:text-white hover:bg-gray-800/50'}
                     ${collapsed ? 'justify-center' : ''}`}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          {!collapsed && <span>Settings</span>}
        </button>
      </div>
    </aside>
  );
}
