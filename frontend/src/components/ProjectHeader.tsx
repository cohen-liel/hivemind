import { Link } from 'react-router-dom';
import type { LoopProgress } from '../types';

interface Props {
  projectName: string;
  status: string;
  connected: boolean;
  totalCost: number;
  turnCount: number;
  agentCount: number;
  progress: LoopProgress | null;
}

const STATUS_BADGE: Record<string, { label: string; color: string }> = {
  running: { label: 'Running', color: 'bg-green-500' },
  paused: { label: 'Paused', color: 'bg-yellow-500' },
  stopped: { label: 'Stopped', color: 'bg-red-500' },
  idle: { label: 'Idle', color: 'bg-gray-500' },
};

export default function ProjectHeader({
  projectName, status, connected, totalCost, turnCount, agentCount, progress,
}: Props) {
  const badge = STATUS_BADGE[status] ?? STATUS_BADGE.idle;

  const turnsUsed = progress?.turn ?? turnCount;
  const turnsMax = progress?.max_turns ?? 0;
  const turnsPct = turnsMax > 0 ? Math.min((turnsUsed / turnsMax) * 100, 100) : 0;

  const costUsed = progress?.cost ?? totalCost;
  const costMax = progress?.max_budget ?? 0;

  return (
    <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-20">
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-3">
        {/* Row 1: Name + status + connection */}
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="text-gray-400 hover:text-white transition-colors text-sm flex-shrink-0"
          >
            &larr;
          </Link>
          <h1 className="text-lg sm:text-xl font-bold text-white truncate">{projectName}</h1>
          <span className={`${badge.color} text-xs font-medium px-2 py-0.5 rounded-full text-white flex-shrink-0`}>
            {badge.label}
          </span>
          <span
            className={`w-2 h-2 rounded-full ml-auto flex-shrink-0 ${connected ? 'bg-green-500' : 'bg-red-500'}`}
            title={connected ? 'Live' : 'Disconnected'}
          />
        </div>

        {/* Row 2: Progress bar + stats */}
        <div className="mt-2 flex items-center gap-4">
          {/* Progress bar */}
          {turnsMax > 0 && status === 'running' && (
            <div className="flex-1 max-w-xs">
              <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-300"
                  style={{ width: `${turnsPct}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
                <span>Turn {turnsUsed}/{turnsMax}</span>
                {progress && <span>Loop {progress.loop}/{progress.max_loops}</span>}
              </div>
            </div>
          )}

          {/* Stats */}
          <div className="flex items-center gap-4 text-xs text-gray-500 ml-auto">
            <span>
              <span className="text-gray-300 font-medium">${costUsed.toFixed(4)}</span>
              {costMax > 0 && <span className="text-gray-700"> / ${costMax.toFixed(2)}</span>}
            </span>
            <span>
              <span className="text-gray-300 font-medium">{turnsUsed}</span> turns
            </span>
            <span>
              <span className="text-gray-300 font-medium">{agentCount}</span> agents
            </span>
          </div>
        </div>
      </div>
    </header>
  );
}
