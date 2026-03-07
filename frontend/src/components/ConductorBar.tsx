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

  // Progress calculation
  const turnsUsed = progress?.turn ?? 0;
  const turnsMax = progress?.max_turns ?? 0;
  const turnsPct = turnsMax > 0 ? Math.min((turnsUsed / turnsMax) * 100, 100) : 0;
  const costUsed = progress?.cost ?? totalCost;

  // Agent summary counts
  const counts = { working: 0, done: 0, error: 0, idle: 0 };
  if (agentSummary) {
    for (const a of agentSummary) {
      if (a.state in counts) counts[a.state as keyof typeof counts]++;
    }
  }
  const hasAgents = agentSummary && agentSummary.length > 0;
  const hasActivity = counts.working > 0 || counts.done > 0 || counts.error > 0;

  return (
    <header className={`relative border-b border-gray-800 bg-gray-900/80 backdrop-blur-md sticky top-0 z-20 transition-all duration-500
      ${isActive ? 'shadow-[0_4px_30px_rgba(59,130,246,0.15)]' : ''}`}>

      {/* Top row: back + project name + connection */}
      <div className="px-4 pt-3 pb-1 flex items-center gap-3">
        <Link
          to="/"
          className="text-gray-500 hover:text-white transition-colors flex-shrink-0"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
        </Link>
        <h1 className="text-base font-bold text-white truncate flex-1">{projectName}</h1>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-[10px] text-gray-600 uppercase">
            {status === 'running' ? 'Live' : status}
          </span>
        </div>
      </div>

      {/* Conductor section */}
      <div className="px-4 pb-3">
        <div className="flex items-center gap-3 mt-1 transition-all duration-500">
          {/* Conductor icon with aura */}
          <div className="relative flex-shrink-0">
            <div className={`w-9 h-9 rounded-full flex items-center justify-center text-base transition-all duration-500
              ${isActive
                ? 'bg-blue-500/20 shadow-[0_0_20px_rgba(59,130,246,0.4)]'
                : isOrchestratorDone
                  ? 'bg-green-500/10'
                  : 'bg-gray-800/50'}`}>
              {'\u{1F3AF}'}
            </div>
            {isActive && (
              <div className="absolute inset-0 rounded-full border-2 border-blue-400/30 animate-ping" />
            )}
          </div>

          {/* Current goal / status text */}
          <div className="flex-1 min-w-0">
            {isActive && orchestrator?.task && (
              <div className="text-xs text-blue-300/90 truncate">
                {orchestrator.task}
              </div>
            )}
            {isActive && orchestrator?.current_tool && (
              <div className="text-[11px] text-gray-500 font-mono truncate mt-0.5">
                {orchestrator.current_tool}
              </div>
            )}
            {!isActive && status === 'running' && (
              <div className="text-xs text-gray-500">Waiting for orchestrator...</div>
            )}
            {status === 'idle' && (
              <div className="text-xs text-gray-600">Send a task to begin</div>
            )}
            {status === 'paused' && (
              <div className="text-xs text-yellow-500/80">Paused</div>
            )}
          </div>

          {/* Cost pill */}
          {costUsed > 0 && (
            <div className="flex-shrink-0 bg-gray-800/60 rounded-full px-2 py-0.5">
              <span className="text-[10px] font-mono text-gray-400">${costUsed.toFixed(3)}</span>
            </div>
          )}
        </div>

        {/* Status Summary: colored dots with counts */}
        {hasAgents && hasActivity && (
          <div className="flex items-center gap-3 mt-2">
            {counts.working > 0 && (
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                <span className="text-[10px] text-blue-400 font-medium">{counts.working} working</span>
              </div>
            )}
            {counts.done > 0 && (
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-green-500" />
                <span className="text-[10px] text-green-400/80 font-medium">{counts.done} done</span>
              </div>
            )}
            {counts.error > 0 && (
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-red-500" />
                <span className="text-[10px] text-red-400 font-medium">{counts.error} error</span>
              </div>
            )}
            {counts.idle > 0 && (
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-gray-600" />
                <span className="text-[10px] text-gray-600 font-medium">{counts.idle} standby</span>
              </div>
            )}
          </div>
        )}

        {/* Progress bar */}
        {turnsMax > 0 && status === 'running' && (
          <div className="mt-2">
            <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full transition-all duration-500"
                style={{ width: `${turnsPct}%` }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-gray-700 mt-0.5">
              <span>Turn {turnsUsed}/{turnsMax}</span>
              {progress && <span>Loop {progress.loop}/{progress.max_loops}</span>}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
