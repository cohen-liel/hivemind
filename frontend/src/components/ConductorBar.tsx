import { Link } from 'react-router-dom';
import type { AgentState, LoopProgress } from '../types';

interface Props {
  projectName: string;
  status: string;
  connected: boolean;
  orchestrator: AgentState | null;
  progress: LoopProgress | null;
  totalCost: number;
  agentSummary?: AgentState[];
}

export default function ConductorBar({
  projectName, status, connected, orchestrator, progress, totalCost, agentSummary,
}: Props) {
  const isActive = orchestrator?.state === 'working';
  const isOrchestratorDone = orchestrator?.state === 'done';

  const turnsUsed = progress?.turn ?? 0;
  const turnsMax = progress?.max_turns ?? 0;
  const turnsPct = turnsMax > 0 ? Math.min((turnsUsed / turnsMax) * 100, 100) : 0;
  const costUsed = progress?.cost ?? totalCost;

  const counts = { working: 0, done: 0, error: 0, idle: 0 };
  if (agentSummary) {
    for (const a of agentSummary) {
      if (a.state in counts) counts[a.state as keyof typeof counts]++;
    }
  }
  const hasAgents = agentSummary && agentSummary.length > 0;
  const hasActivity = counts.working > 0 || counts.done > 0 || counts.error > 0;

  return (
    <header
      className="relative flex-shrink-0 sticky top-0 z-20 transition-all duration-500"
      style={{
        background: 'var(--bg-panel)',
        borderBottom: '1px solid var(--border-dim)',
        backdropFilter: 'blur(12px)',
        boxShadow: isActive ? '0 4px 30px var(--glow-blue)' : 'none',
      }}
    >
      {/* Active glow bar */}
      {isActive && (
        <div
          className="absolute bottom-0 left-0 h-[2px] animate-[loading_3s_ease-in-out_infinite]"
          style={{
            width: '40%',
            background: 'linear-gradient(90deg, transparent, var(--accent-blue), transparent)',
          }}
        />
      )}

      {/* Main row */}
      <div className="px-3 py-2 flex items-center gap-2.5">
        <Link
          to="/"
          className="transition-colors flex-shrink-0 rounded-lg p-1 hover:bg-[var(--bg-elevated)]"
          style={{ color: 'var(--text-muted)' }}
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
            🎯
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
            <div className="flex items-center gap-1 flex-shrink-0">
              <span
                className={`w-1.5 h-1.5 rounded-full ${connected ? '' : ''}`}
                style={{ backgroundColor: connected ? 'var(--accent-green)' : 'var(--accent-red)' }}
              />
              <span className="text-[9px] uppercase tracking-wider"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {status === 'running' ? 'LIVE' : status.toUpperCase()}
              </span>
            </div>
          </div>

          {/* Orchestrator status line */}
          {isActive && orchestrator?.current_tool && (
            <div className="text-[10px] truncate text-fade-right"
              style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)', opacity: 0.7 }}>
              {orchestrator.current_tool}
            </div>
          )}
          {isActive && !orchestrator?.current_tool && orchestrator?.task && (
            <div className="text-[10px] truncate" style={{ color: 'var(--accent-blue)', opacity: 0.7 }}>
              {orchestrator.task}
            </div>
          )}
          {!isActive && status === 'idle' && (
            <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Send a task to begin</div>
          )}
          {status === 'paused' && (
            <div className="text-[10px]" style={{ color: 'var(--accent-amber)' }}>Paused — waiting for input</div>
          )}
        </div>

        {/* Cost pill */}
        {costUsed > 0 && (
          <div className="flex-shrink-0 rounded-full px-2.5 py-0.5"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
            <span className="telemetry" style={{ color: 'var(--accent-green)' }}>
              ${costUsed.toFixed(3)}
            </span>
          </div>
        )}
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
