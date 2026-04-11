import { Link, useNavigate } from 'react-router-dom';
import { deleteProject } from '../api';
import type { AgentState, LoopProgress } from '../types';

interface Props {
  projectId: string;
  projectName: string;
  status: string;
  connected: boolean;
  orchestrator: AgentState | null;
  progress: LoopProgress | null;
  agentSummary?: AgentState[];
  /** Most recent live activity text from any agent — shown when orchestrator has no specific task */
  lastTicker?: string;
}

export default function ConductorBar({
  projectId, projectName, status, connected, orchestrator, progress, agentSummary, lastTicker,
}: Props) {
  const navigate = useNavigate();
  const isActive = orchestrator?.state === 'working' || orchestrator?.state === 'waiting';
  const isOrchestratorDone = orchestrator?.state === 'done';

  const turnsUsed = progress?.turn ?? 0;
  const turnsMax = progress?.max_turns ?? 0;
  const turnsPct = turnsMax > 0 ? Math.min((turnsUsed / turnsMax) * 100, 100) : 0;

  const counts = { working: 0, done: 0, error: 0, idle: 0 };
  if (agentSummary) {
    for (const a of agentSummary) {
      // Treat 'waiting' as 'working' for display purposes
      const displayState = a.state === 'waiting' ? 'working' : a.state;
      if (displayState in counts) counts[displayState as keyof typeof counts]++;
    }
  }
  const hasAgents = agentSummary && agentSummary.length > 0;
  const hasActivity = counts.working > 0 || counts.done > 0 || counts.error > 0;

  return (
    <header
      className="relative flex-shrink-0 sticky top-0 z-20 transition-all duration-500"
      style={{
        background: isActive
          ? 'linear-gradient(180deg, rgba(99, 140, 255, 0.04) 0%, var(--bg-panel) 100%)'
          : 'var(--bg-panel)',
        borderBottom: '1px solid var(--border-dim)',
        backdropFilter: 'blur(12px)',
        boxShadow: isActive ? '0 4px 30px var(--glow-blue)' : 'none',
      }}
    >
      {/* Active glow bar — shows during orchestration and during startup */}
      {(isActive || status === 'running') && (
        <div
          className="absolute bottom-0 left-0 h-[2px] animate-[loading_3s_ease-in-out_infinite]"
          style={{
            width: '40%',
            background: isActive
              ? 'linear-gradient(90deg, transparent, var(--accent-blue), transparent)'
              : 'linear-gradient(90deg, transparent, var(--accent-amber), transparent)',
          }}
        />
      )}

      {/* Main row */}
      <div className="px-3 py-2 flex items-center gap-2.5">
        <Link
          to="/"
          className="transition-colors flex-shrink-0 rounded-lg p-1 hover:bg-[var(--bg-elevated)]"
          style={{ color: 'var(--text-muted)' }}
          aria-label="Back to dashboard"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
        </Link>

        {/* Conductor icon */}
        <div className="relative flex-shrink-0">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-sm transition-all duration-500"
            style={{
              background: isActive
                ? 'var(--glow-blue)'
                : isOrchestratorDone
                  ? 'var(--glow-green)'
                  : 'var(--bg-elevated)',
              boxShadow: isActive ? '0 0 15px var(--glow-blue)' : 'none',
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={isActive ? 'var(--accent-blue)' : isOrchestratorDone ? 'var(--accent-green)' : 'var(--text-muted)'} strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="10" opacity="0.3"/>
              <circle cx="12" cy="12" r="6" opacity="0.5"/>
              <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/>
            </svg>
          </div>
          {isActive && (
            <div
              className="absolute inset-0 rounded-full animate-ping"
              style={{ border: '2px solid var(--accent-blue)', opacity: 0.3 }}
            />
          )}
        </div>

        {/* Project name + status */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-bold truncate" style={{ color: 'var(--text-primary)' }}>
              {projectName}
            </h1>
            {/* Connection + status indicator */}
            <div className="flex items-center gap-1 flex-shrink-0">
              <span
                className={`w-1.5 h-1.5 rounded-full transition-colors duration-300 ${connected && status === 'running' ? 'animate-pulse' : ''}`}
                style={{ backgroundColor: connected ? 'var(--accent-green)' : 'var(--accent-red)' }}
              />
              <span className="text-[9px] uppercase tracking-wider"
                style={{
                  color: !connected ? 'var(--accent-red)' : 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                }}>
                {!connected ? 'RECONNECTING' : status === 'running' ? 'LIVE' : status.toUpperCase()}
              </span>
            </div>
          </div>

          {/* Orchestrator status line — show most specific info available */}
          {isActive && (
            <div className="flex items-center gap-1.5">
              <div className="w-1 h-1 rounded-full animate-pulse flex-shrink-0"
                style={{ background: 'var(--accent-blue)' }} />
              <div className="text-[10px] truncate"
                style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
                {orchestrator?.current_tool || orchestrator?.task || lastTicker || 'orchestrating...'}
              </div>
            </div>
          )}
          {!isActive && status === 'running' && (
            <div className="flex items-center gap-1.5">
              <div className="w-1 h-1 rounded-full animate-pulse flex-shrink-0"
                style={{ background: 'var(--accent-amber)' }} />
              <div className="text-[10px] truncate"
                style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
                {lastTicker || 'initializing agents...'}
              </div>
            </div>
          )}
          {!isActive && status === 'idle' && (
            <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Send a task to begin</div>
          )}
          {status === 'paused' && (
            <div className="text-[10px]" style={{ color: 'var(--accent-amber)' }}>Paused — waiting for input</div>
          )}
        </div>

        {/* DAG visualization link */}
        <Link
          to={`/projects/${projectId}/dag`}
          className="flex-shrink-0 p-1.5 rounded-lg transition-all hover:bg-[var(--bg-elevated)]"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent-blue, #638cff)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          title="View DAG"
          aria-label="View DAG visualization"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="5" cy="12" r="2"/>
            <circle cx="12" cy="5" r="2"/>
            <circle cx="12" cy="19" r="2"/>
            <circle cx="19" cy="12" r="2"/>
            <path d="M7 12h4M12 7v4M12 15v4M14 12h3"/>
          </svg>
        </Link>

        {/* Delete project button */}
        <button
          onClick={() => {
            if (confirm(`Delete "${projectName}"?\n\nThis will permanently remove the project and all its data.`)) {
              deleteProject(projectId)
                .then(() => {
                  navigate('/');
                })
                .catch((err) => {
                  console.error('Failed to delete project:', err);
                  alert('Failed to delete project. Please try again.');
                });
            }
          }}
          className="flex-shrink-0 p-1.5 rounded-lg transition-all hover:bg-[rgba(239,68,68,0.1)]"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent-red, #ef4444)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          title="Remove project"
          aria-label={`Remove project ${projectName}`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
            <line x1="10" y1="11" x2="10" y2="17"/>
            <line x1="14" y1="11" x2="14" y2="17"/>
          </svg>
        </button>
      </div>

      {/* Agent status + progress bar */}
      {(hasActivity || (turnsMax > 0 && status === 'running')) && (
        <div className="px-3 pb-2 flex items-center gap-3">
          {hasAgents && hasActivity && (
            <div className="flex items-center gap-2.5">
              {counts.working > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: 'var(--accent-blue)' }} />
                  <span className="text-[9px] font-medium" style={{ color: 'var(--accent-blue)' }}>{counts.working}</span>
                </div>
              )}
              {counts.done > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--accent-green)' }} />
                  <span className="text-[9px] font-medium" style={{ color: 'var(--accent-green)', opacity: 0.8 }}>{counts.done}</span>
                </div>
              )}
              {counts.error > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--accent-red)' }} />
                  <span className="text-[9px] font-medium" style={{ color: 'var(--accent-red)' }}>{counts.error}</span>
                </div>
              )}
              {counts.idle > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--text-muted)' }} />
                  <span className="text-[9px] font-medium" style={{ color: 'var(--text-muted)' }}>{counts.idle}</span>
                </div>
              )}
            </div>
          )}

          {/* Progress bar */}
          {turnsMax > 0 && status === 'running' && (
            <div className="flex-1 flex items-center gap-2">
              <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--border-dim)' }}>
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${turnsPct}%`,
                    background: `linear-gradient(90deg, var(--accent-blue), var(--accent-cyan))`,
                  }}
                />
              </div>
              <span className="telemetry flex-shrink-0" style={{ fontSize: '8px', color: 'var(--text-muted)' }}>
                {turnsUsed}/{turnsMax}
              </span>
            </div>
          )}
        </div>
      )}
    </header>
  );
}
