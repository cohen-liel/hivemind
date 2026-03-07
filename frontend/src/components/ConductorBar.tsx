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
    <header className={`relative flex-shrink-0 border-b border-gray-800 bg-gray-900/80 backdrop-blur-md sticky top-0 z-20 transition-all duration-500
      ${isActive ? 'shadow-[0_4px_30px_rgba(59,130,246,0.15)]' : ''}`}>

      {/* Single compact row: back + conductor icon + info + status */}
      <div className="px-3 py-2 flex items-center gap-2.5">
        <Link
          to="/"
          className="text-gray-500 hover:text-white transition-colors flex-shrink-0"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
        </Link>

        {/* Conductor icon */}
        <div className="relative flex-shrink-0">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm transition-all duration-500
            ${isActive
              ? 'bg-blue-500/20 shadow-[0_0_15px_rgba(59,130,246,0.4)]'
              : isOrchestratorDone
                ? 'bg-green-500/10'
                : 'bg-gray-800/50'}`}>
            {'\u{1F3AF}'}
          </div>
          {isActive && (
            <div className="absolute inset-0 rounded-full border-2 border-blue-400/30 animate-ping" />
          )}
        </div>

        {/* Project name + status text */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-bold text-white truncate">{projectName}</h1>
            <div className="flex items-center gap-1 flex-shrink-0">
              <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
              <span className="text-[9px] text-gray-600 uppercase">
                {status === 'running' ? 'Live' : status}
              </span>
            </div>
          </div>
          {isActive && orchestrator?.current_tool && (
            <div className="text-[10px] text-blue-300/70 font-mono truncate">
              {orchestrator.current_tool}
            </div>
          )}
          {isActive && !orchestrator?.current_tool && orchestrator?.task && (
            <div className="text-[10px] text-blue-300/70 truncate">
              {orchestrator.task}
            </div>
          )}
          {!isActive && status === 'idle' && (
            <div className="text-[10px] text-gray-700">Send a task to begin</div>
          )}
          {status === 'paused' && (
            <div className="text-[10px] text-yellow-500/80">Paused</div>
          )}
        </div>

        {/* Cost pill */}
        {costUsed > 0 && (
          <div className="flex-shrink-0 bg-gray-800/60 rounded-full px-2 py-0.5">
            <span className="text-[9px] font-mono text-gray-400">${costUsed.toFixed(3)}</span>
          </div>
        )}
      </div>

      {/* Status summary + progress (compact row, only when active) */}
      {(hasActivity || (turnsMax > 0 && status === 'running')) && (
        <div className="px-3 pb-2 flex items-center gap-3">
          {/* Colored dot summary */}
          {hasAgents && hasActivity && (
            <div className="flex items-center gap-2">
              {counts.working > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                  <span className="text-[9px] text-blue-400 font-medium">{counts.working}</span>
                </div>
              )}
              {counts.done > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  <span className="text-[9px] text-green-400/80 font-medium">{counts.done}</span>
                </div>
              )}
              {counts.error > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                  <span className="text-[9px] text-red-400 font-medium">{counts.error}</span>
                </div>
              )}
              {counts.idle > 0 && (
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-600" />
                  <span className="text-[9px] text-gray-600 font-medium">{counts.idle}</span>
                </div>
              )}
            </div>
          )}

          {/* Progress bar */}
          {turnsMax > 0 && status === 'running' && (
            <div className="flex-1 flex items-center gap-2">
              <div className="flex-1 h-1 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full transition-all duration-500"
                  style={{ width: `${turnsPct}%` }}
                />
              </div>
              <span className="text-[8px] text-gray-700 flex-shrink-0">
                {turnsUsed}/{turnsMax}
              </span>
            </div>
          )}
        </div>
      )}
    </header>
  );
}
