import { useEffect, useReducer, useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getProjects, getTasks, deleteProject, updateProject } from '../api';
import { useWSSubscribe } from '../WebSocketContext';
import { AGENT_ICONS } from '../constants';
import { DashboardSkeleton } from '../components/Skeleton';
import ErrorState from '../components/ErrorState';
import { useToast } from '../components/Toast';
import CostChart from '../components/CostChart';
import AgentLogPanel from '../components/AgentLogPanel';
import { useAnimatedNumber, formatCost } from '../hooks/useAnimatedNumber';
import { usePageTitle } from '../hooks/usePageTitle';
import { useTheme } from '../ThemeContext';
import type { Project, WSEvent, TaskHistoryItem } from '../types';

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

// ============================================================================
// Dashboard State (STATE-02: replaces 8 individual useState hooks)
// ============================================================================

interface DashboardLiveState {
  text: string;
  agent?: string;
  activeAgents: Set<string>;
}

interface DashboardState {
  projects: Project[];
  liveStates: Record<string, DashboardLiveState>;
  searchQuery: string;
  statusFilter: string;
  logExpandedId: string | null;
  loading: boolean;
  error: string | null;
  costExpanded: boolean;
}

const initialDashboardState: DashboardState = {
  projects: [],
  liveStates: {},
  searchQuery: '',
  statusFilter: 'all',
  logExpandedId: null,
  loading: true,
  error: null,
  costExpanded: false,
};

type DashboardAction =
  | { type: 'LOAD_SUCCESS'; projects: Project[] }
  | { type: 'LOAD_ERROR'; error: string }
  | { type: 'CLEAR_LOADING' }
  | { type: 'SET_SEARCH'; query: string }
  | { type: 'SET_STATUS_FILTER'; filter: string }
  | { type: 'TOGGLE_LOG'; projectId: string }
  | { type: 'TOGGLE_COST_PANEL' }
  | { type: 'SET_PROJECT_STATUS'; projectId: string; status: Project['status'] }
  | { type: 'SET_LIVE_STATE'; projectId: string; liveState: DashboardLiveState }
  | { type: 'CLEAR_LIVE_STATE'; projectId: string }
  | { type: 'AGENT_STARTED'; projectId: string; agent: string; text: string }
  | { type: 'AGENT_FINISHED'; projectId: string; agent: string; text: string }
  | { type: 'REMOVE_PROJECT'; projectId: string }
  | { type: 'RENAME_PROJECT'; projectId: string; name: string };

function dashboardReducer(state: DashboardState, action: DashboardAction): DashboardState {
  switch (action.type) {
    case 'LOAD_SUCCESS':
      return {
        ...state,
        projects: action.projects,
        error: null,
        loading: false,
      };
    case 'LOAD_ERROR':
      return {
        ...state,
        error: action.error,
        loading: false,
      };
    case 'CLEAR_LOADING':
      return { ...state, loading: false };
    case 'SET_SEARCH':
      return { ...state, searchQuery: action.query };
    case 'SET_STATUS_FILTER':
      return { ...state, statusFilter: action.filter };
    case 'TOGGLE_LOG':
      return {
        ...state,
        logExpandedId: state.logExpandedId === action.projectId ? null : action.projectId,
      };
    case 'TOGGLE_COST_PANEL':
      return { ...state, costExpanded: !state.costExpanded };
    case 'SET_PROJECT_STATUS':
      return {
        ...state,
        projects: state.projects.map(p =>
          p.project_id === action.projectId ? { ...p, status: action.status } : p,
        ),
      };
    case 'SET_LIVE_STATE': {
      const existing = state.liveStates[action.projectId];
      return {
        ...state,
        liveStates: {
          ...state.liveStates,
          [action.projectId]: {
            ...action.liveState,
            // Preserve activeAgents from existing state when the caller
            // passes an empty set (e.g. tool_use events that only update text)
            activeAgents: action.liveState.activeAgents.size > 0
              ? action.liveState.activeAgents
              : existing?.activeAgents ?? new Set<string>(),
          },
        },
      };
    }
    case 'AGENT_STARTED': {
      const prev = state.liveStates[action.projectId] ?? { text: '', activeAgents: new Set<string>() };
      const active = new Set(prev.activeAgents);
      active.add(action.agent);
      return {
        ...state,
        projects: state.projects.map(p =>
          p.project_id === action.projectId ? { ...p, status: 'running' as const } : p,
        ),
        liveStates: {
          ...state.liveStates,
          [action.projectId]: { text: action.text, agent: action.agent, activeAgents: active },
        },
      };
    }
    case 'AGENT_FINISHED': {
      const prev = state.liveStates[action.projectId] ?? { text: '', activeAgents: new Set<string>() };
      const active = new Set(prev.activeAgents);
      active.delete(action.agent);
      return {
        ...state,
        liveStates: {
          ...state.liveStates,
          [action.projectId]: { text: action.text, agent: action.agent, activeAgents: active },
        },
      };
    }
    case 'CLEAR_LIVE_STATE': {
      const next = { ...state.liveStates };
      delete next[action.projectId];
      return { ...state, liveStates: next };
    }
    case 'REMOVE_PROJECT':
      return {
        ...state,
        projects: state.projects.filter(p => p.project_id !== action.projectId),
      };
    case 'RENAME_PROJECT':
      return {
        ...state,
        projects: state.projects.map(p =>
          p.project_id === action.projectId ? { ...p, project_name: action.name } : p,
        ),
      };
    default:
      return state;
  }
}

// ============================================================================
// Component
// ============================================================================

export default function Dashboard(): React.ReactElement {
  const toast = useToast();
  const [state, dispatch] = useReducer(dashboardReducer, initialDashboardState);
  const {
    projects, liveStates, searchQuery, statusFilter,
    logExpandedId, loading, error, costExpanded,
  } = state;

  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();

  // Last task per project — fetched alongside project data
  const [lastTasks, setLastTasks] = useState<Record<string, TaskHistoryItem>>({});

  // Inline rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  // Prevents onBlur from double-saving when Enter already committed the rename
  const renameSavedRef = useRef(false);

  // Dynamic page title
  usePageTitle('Dashboard');

  // Use a ref to track whether we have data, avoiding the dependency on
  // projects.length which would cause loadData to change on every fetch
  // and create an infinite polling loop.
  const hasDataRef = useRef(false);

  const loadData = useCallback(async () => {
    try {
      const p = await getProjects();
      dispatch({ type: 'LOAD_SUCCESS', projects: p });
      hasDataRef.current = p.length > 0;

      // Fetch last task for each project (fire-and-forget, non-blocking)
      const taskMap: Record<string, TaskHistoryItem> = {};
      await Promise.allSettled(
        p.map(async (proj) => {
          try {
            const tasks = await getTasks(proj.project_id);
            if (tasks.length > 0) taskMap[proj.project_id] = tasks[0];
          } catch { /* ignore per-project failures */ }
        }),
      );
      setLastTasks(taskMap);
    } catch (e: unknown) {
      if (!hasDataRef.current) {
        dispatch({ type: 'LOAD_ERROR', error: e instanceof Error ? e.message : 'Failed to load' });
      } else {
        dispatch({ type: 'CLEAR_LOADING' });
      }
    }
  }, []);

  useEffect(() => {
    loadData();
    // Poll every 30 seconds as a reliability fallback when WebSocket is disconnected.
    // The WS handler keeps data fresh in real-time; this ensures stale state
    // is corrected after prolonged disconnection or tab backgrounding.
    const poll = setInterval(loadData, 30_000);
    return () => clearInterval(poll);
  }, [loadData]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (!event.project_id) return;
    const pid = event.project_id;

    if (event.type === 'tool_use' && event.description) {
      // tool_use only updates the text — activeAgents are managed by
      // AGENT_STARTED/AGENT_FINISHED which read from reducer state directly,
      // avoiding stale closure issues.
      dispatch({
        type: 'SET_LIVE_STATE',
        projectId: pid,
        liveState: {
          text: event.description!,
          agent: event.agent,
          // Preserve existing activeAgents — the reducer reads current state
          activeAgents: new Set(),
        },
      });
    } else if (event.type === 'agent_started' && event.agent) {
      dispatch({
        type: 'AGENT_STARTED',
        projectId: pid,
        agent: event.agent,
        text: event.task ? `${event.agent}: ${event.task.slice(0, 80)}` : `${event.agent} started`,
      });
    } else if (event.type === 'agent_finished' && event.agent) {
      dispatch({
        type: 'AGENT_FINISHED',
        projectId: pid,
        agent: event.agent,
        text: `${event.agent} ${event.is_error ? 'failed' : 'done'}`,
      });
    } else if (event.type === 'project_status' && event.status) {
      dispatch({ type: 'SET_PROJECT_STATUS', projectId: pid, status: event.status as Project['status'] });
      loadData();
    } else if (event.type === 'agent_final') {
      loadData();
      dispatch({ type: 'CLEAR_LIVE_STATE', projectId: pid });
    }
  }, [loadData]);

  const { connected } = useWSSubscribe(handleWSEvent);

  // Memoised derivations — only recompute when projects/filters actually change
  const filteredProjects = useMemo(() => projects.filter(p => {
    if (statusFilter !== 'all' && p.status !== statusFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return p.project_name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q);
    }
    return true;
  }), [projects, statusFilter, searchQuery]);

  const statusConfig = useMemo(() => (status: string): {
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
  }, []);  // statusConfig has no external deps — stable reference

  const runningCount = useMemo(() => projects.filter(p => p.status === 'running').length, [projects]);
  const totalCost = useMemo(() => projects.reduce((sum, p) => sum + (p.total_cost_usd || 0), 0), [projects]);

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
          onRetry={() => { dispatch({ type: 'CLEAR_LOADING' }); loadData(); }}
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
                    Hivemind
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

              {/* Dark mode toggle — visible on all screen sizes */}
              <button
                type="button"
                onClick={toggleTheme}
                aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                className="p-2 rounded-xl transition-all duration-200 active:scale-90"
                style={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-dim)',
                  color: 'var(--text-secondary)',
                }}
                onFocus={e => { e.currentTarget.style.outline = '2px solid var(--focus-ring)'; e.currentTarget.style.outlineOffset = '2px'; }}
                onBlur={e => { e.currentTarget.style.outline = 'none'; }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-card)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
              >
                {theme === 'dark' ? (
                  /* Sun icon */
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.3"/>
                    <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"
                      stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
                  </svg>
                ) : (
                  /* Moon icon */
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M13.5 9.5a5.5 5.5 0 01-7-7 5.5 5.5 0 107 7z"
                      stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </button>
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
              onChange={(e) => dispatch({ type: 'SET_SEARCH', query: e.target.value })}
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
                onClick={() => dispatch({ type: 'SET_STATUS_FILTER', filter: st })}
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
              aria-label="Welcome to Hivemind — no projects yet"
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
                Welcome to Hivemind
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
        ) : filteredProjects.length === 0 ? (
          <div className="text-center py-16" style={{ color: 'var(--text-muted)' }}>
            <p className="text-sm mb-1">No projects match your filter</p>
            <p className="text-xs" style={{ fontFamily: 'var(--font-mono)' }}>
              Try adjusting your search or status filter
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredProjects.map((project, i) => {
              const cfg = statusConfig(project.status);
              const live = liveStates[project.project_id];
              const subAgents = (project.agents || []).filter(a => a !== 'orchestrator');
              const lastActivity = formatRelativeTime(
                project.updated_at || project.last_message?.timestamp || project.created_at
              );
              const isLogExpanded = logExpandedId === project.project_id;
              const lastTask = lastTasks[project.project_id];

              return (
                /* Wrapper div: card + optional log panel stacked vertically */
                <div key={project.project_id} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>

                  {/* ── Project card (div role=button for keyboard nav) ── */}
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(`/project/${project.project_id}`)}
                    onKeyDown={(e: React.KeyboardEvent<HTMLDivElement>) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        navigate(`/project/${project.project_id}`);
                      }
                    }}
                    aria-label={`Open project ${project.project_name}, status: ${cfg.label}, ${subAgents.length} agents`}
                    className={`text-left cursor-pointer transition-all duration-300 group rounded-2xl p-4 sm:p-5 card-hover ${cfg.cardClass}`}
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border-dim)',
                      animation: `slideUp 0.3s ease-out ${i * 60}ms backwards`,
                      outline: 'none',
                    }}
                    onFocus={e => {
                      e.currentTarget.style.outline = '2px solid var(--focus-ring)';
                      e.currentTarget.style.outlineOffset = '2px';
                    }}
                    onBlur={e => {
                      e.currentTarget.style.outline = 'none';
                    }}
                  >
                    {/* Header: name + status badge + actions */}
                    <div className="flex items-center justify-between mb-3 gap-2">
                      {renamingId === project.project_id ? (
                        <input
                          autoFocus
                          value={renameValue}
                          onChange={e => setRenameValue(e.target.value)}
                          onKeyDown={e => {
                            e.stopPropagation();
                            if (e.key === 'Enter') {
                              renameSavedRef.current = true;
                              const trimmed = renameValue.trim();
                              if (trimmed && trimmed !== project.project_name) {
                                updateProject(project.project_id, { name: trimmed })
                                  .then(() => dispatch({ type: 'RENAME_PROJECT', projectId: project.project_id, name: trimmed }))
                                  .catch(() => toast.error('Rename failed', 'Could not rename project'));
                              }
                              setRenamingId(null);
                            } else if (e.key === 'Escape') {
                              renameSavedRef.current = true;
                              setRenamingId(null);
                            }
                          }}
                          onBlur={() => {
                            // Skip if Enter/Escape already handled this
                            if (renameSavedRef.current) {
                              renameSavedRef.current = false;
                              return;
                            }
                            const trimmed = renameValue.trim();
                            if (trimmed && trimmed !== project.project_name) {
                              updateProject(project.project_id, { name: trimmed })
                                .then(() => dispatch({ type: 'RENAME_PROJECT', projectId: project.project_id, name: trimmed }))
                                .catch(() => toast.error('Rename failed', 'Could not rename project'));
                            }
                            setRenamingId(null);
                          }}
                          onClick={e => e.stopPropagation()}
                          className="text-sm sm:text-base font-bold min-w-0 px-2 py-0.5 rounded-lg focus:outline-none"
                          style={{
                            color: 'var(--text-primary)',
                            background: 'var(--bg-elevated)',
                            border: '1px solid var(--accent-blue)',
                            width: '100%',
                            maxWidth: '300px',
                          }}
                        />
                      ) : (
                        <h3 className="text-sm sm:text-base font-bold truncate min-w-0 transition-colors"
                          style={{ color: 'var(--text-primary)' }}>
                          {project.project_name}
                        </h3>
                      )}
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <div
                          className="flex items-center gap-1.5 px-2.5 py-1 rounded-full status-badge-pop"
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
                        {/* Rename button */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(project.project_id);
                            setRenameValue(project.project_name);
                          }}
                          className="opacity-0 group-hover:opacity-70 hover:!opacity-100 p-1.5 rounded-lg transition-all"
                          style={{ color: 'var(--text-muted)' }}
                          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent-blue, #638cff)'; e.currentTarget.style.background = 'rgba(99,140,255,0.1)'; }}
                          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
                          title="Rename project"
                          aria-label={`Rename project ${project.project_name}`}
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
                            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
                          </svg>
                        </button>
                        {/* Delete button */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (confirm(`Remove "${project.project_name}" from dashboard?\n\nThis only removes it from the list — project files are NOT deleted.`)) {
                              deleteProject(project.project_id)
                                .then(() => dispatch({ type: 'REMOVE_PROJECT', projectId: project.project_id }))
                                .catch(() => toast.error('Delete failed', 'Could not remove project'));
                            }
                          }}
                          className="opacity-0 group-hover:opacity-70 hover:!opacity-100 p-1.5 rounded-lg transition-all"
                          style={{ color: 'var(--text-muted)' }}
                          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent-red, #ef4444)'; e.currentTarget.style.background = 'rgba(239,68,68,0.1)'; }}
                          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
                          title="Remove project"
                          aria-label={`Remove project ${project.project_name}`}
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="3 6 5 6 21 6"/>
                            <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                            <line x1="10" y1="11" x2="10" y2="17"/>
                            <line x1="14" y1="11" x2="14" y2="17"/>
                          </svg>
                        </button>
                      </div>
                    </div>

                    {/* Description */}
                    {project.description && !project.description.startsWith('Predefined project:') && !project.description.startsWith('Project:') && (
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

                    {/* DAG Progress Bar */}
                    {project.dag_progress && project.dag_progress.total > 0 && (
                      <div className="mb-3">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[10px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                            DAG PROGRESS
                          </span>
                          <span className="text-[10px] font-bold" style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
                            {project.dag_progress.completed}/{project.dag_progress.total} tasks
                            {project.dag_progress.failed > 0 && (
                              <span style={{ color: 'var(--accent-red, #ff6b6b)' }}> ({project.dag_progress.failed} failed)</span>
                            )}
                          </span>
                        </div>
                        <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${project.dag_progress.percent}%`,
                              background: project.dag_progress.failed > 0
                                ? 'linear-gradient(90deg, var(--accent-green) 0%, var(--accent-red, #ff6b6b) 100%)'
                                : 'linear-gradient(90deg, var(--accent-blue) 0%, var(--accent-green) 100%)',
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* DAG Vision / Plan Summary */}
                    {project.dag_vision && (
                      <p className="text-[10px] mb-3 leading-relaxed line-clamp-2 px-2 py-1.5 rounded-lg"
                        style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', border: '1px solid var(--border-dim)' }}>
                        🎯 {project.dag_vision.slice(0, 150)}
                      </p>
                    )}

                    {/* Health Warnings */}
                    {project.diagnostics && project.diagnostics.health_score && project.diagnostics.health_score !== 'healthy' ? (
                      <div className="flex items-center gap-1.5 mb-3 px-2 py-1.5 rounded-lg text-[10px]"
                        style={{ 
                          background: project.diagnostics.health_score === 'critical' ? 'rgba(255,107,107,0.08)' : 'rgba(245,166,35,0.08)', 
                          border: `1px solid ${project.diagnostics.health_score === 'critical' ? 'rgba(255,107,107,0.15)' : 'rgba(245,166,35,0.15)'}`, 
                          color: project.diagnostics.health_score === 'critical' ? 'var(--accent-red, #ff6b6b)' : 'var(--accent-amber)' 
                        }}>
                        {project.diagnostics.health_score === 'critical' ? '🔴' : '⚠️'}
                        <span>
                          {project.diagnostics.health_score === 'critical' ? 'Agent stalled' : 'Degraded'}
                          {project.diagnostics.seconds_since_progress != null && project.diagnostics.seconds_since_progress > 30 
                            ? ` — no progress for ${Math.round(project.diagnostics.seconds_since_progress)}s` 
                            : ''}
                        </span>
                        {project.diagnostics.warnings_count ? (
                          <span>({project.diagnostics.warnings_count} warning{project.diagnostics.warnings_count > 1 ? 's' : ''})</span>
                        ) : null}
                      </div>
                    ) : null}

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

                    {/* Last session info */}
                    {lastTask && (
                      <div className="mt-2 pt-2 flex items-start gap-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
                        <span
                          className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full flex-shrink-0 mt-0.5"
                          style={{
                            background: lastTask.status === 'completed'
                              ? 'rgba(61,214,140,0.1)' : lastTask.status === 'running'
                              ? 'rgba(99,140,255,0.1)' : 'rgba(245,166,35,0.1)',
                            color: lastTask.status === 'completed'
                              ? 'var(--accent-green)' : lastTask.status === 'running'
                              ? 'var(--accent-blue)' : 'var(--accent-amber)',
                            border: `1px solid ${lastTask.status === 'completed'
                              ? 'rgba(61,214,140,0.15)' : lastTask.status === 'running'
                              ? 'rgba(99,140,255,0.15)' : 'rgba(245,166,35,0.15)'}`,
                          }}
                        >
                          {lastTask.status === 'completed' ? 'Done' : lastTask.status === 'running' ? 'Running' : lastTask.status}
                        </span>
                        <p
                          className="text-[11px] leading-relaxed line-clamp-2 min-w-0"
                          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                        >
                          {lastTask.task_description.slice(0, 120)}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* ── Log toggle button (sibling, NOT nested in card) ── */}
                  <button
                    type="button"
                    onClick={() => dispatch({ type: 'TOGGLE_LOG', projectId: project.project_id })}
                    aria-expanded={isLogExpanded}
                    aria-controls={`log-panel-${project.project_id}`}
                    aria-label={`${isLogExpanded ? 'Collapse' : 'Expand'} agent log for ${project.project_name}`}
                    className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-[11px] font-medium transition-all duration-200"
                    style={{
                      background: isLogExpanded ? 'var(--bg-elevated)' : 'transparent',
                      border: '1px solid var(--border-dim)',
                      color: isLogExpanded ? 'var(--text-primary)' : 'var(--text-muted)',
                      fontFamily: 'var(--font-mono)',
                      outline: 'none',
                    }}
                    onFocus={e => {
                      e.currentTarget.style.outline = '2px solid var(--focus-ring)';
                      e.currentTarget.style.outlineOffset = '2px';
                    }}
                    onBlur={e => {
                      e.currentTarget.style.outline = 'none';
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = isLogExpanded ? 'var(--bg-elevated)' : 'transparent'; e.currentTarget.style.color = isLogExpanded ? 'var(--text-primary)' : 'var(--text-muted)'; }}
                  >
                    {/* Chevron */}
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 10 10"
                      fill="none"
                      aria-hidden="true"
                      style={{
                        transform: isLogExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                        transition: 'transform 0.2s ease',
                        flexShrink: 0,
                      }}
                    >
                      <path d="M1.5 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {isLogExpanded ? 'Hide agent log' : 'Show agent log'}
                  </button>

                  {/* ── Per-project agent log panel ── */}
                  {isLogExpanded && (
                    <div
                      id={`log-panel-${project.project_id}`}
                      style={{ animation: 'fadeSlideIn 0.2s ease-out' }}
                    >
                      <AgentLogPanel projectId={project.project_id} />
                    </div>
                  )}
                </div>
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
              onClick={() => dispatch({ type: 'TOGGLE_COST_PANEL' })}
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
