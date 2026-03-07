interface SdkCall {
  agent: string;
  startTime: number;
  endTime?: number;
  cost?: number;
  status: string;
}

interface Props {
  calls: SdkCall[];
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(start: number, end?: number): string {
  if (!end) return 'running...';
  const ms = (end - start) * 1000;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const AGENT_COLORS: Record<string, string> = {
  developer: 'text-blue-400',
  reviewer: 'text-purple-400',
  tester: 'text-amber-400',
  devops: 'text-cyan-400',
  orchestrator: 'text-gray-400',
};

const STATUS_BADGES: Record<string, { bg: string; text: string; label: string }> = {
  running: { bg: 'bg-blue-500/20', text: 'text-blue-400', label: 'Running' },
  done: { bg: 'bg-green-500/20', text: 'text-green-400', label: 'Done' },
  error: { bg: 'bg-red-500/20', text: 'text-red-400', label: 'Error' },
};

export default function NetworkTrace({ calls }: Props) {
  if (calls.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm px-4">
        <div className="w-12 h-12 rounded-full bg-gray-800 flex items-center justify-center mb-3 text-xl">
          {'\u{1F4E1}'}
        </div>
        <p className="font-medium text-gray-400">No API calls yet</p>
        <p className="text-gray-600 text-xs mt-1">SDK calls will appear here as agents work</p>
      </div>
    );
  }

  const totalCost = calls.reduce((sum, c) => sum + (c.cost || 0), 0);

  return (
    <div className="p-3">
      {/* Summary bar */}
      <div className="flex items-center gap-4 mb-3 px-1">
        <span className="text-xs text-gray-500">{calls.length} calls</span>
        {totalCost > 0 && (
          <span className="text-xs text-gray-500 font-mono">${totalCost.toFixed(4)}</span>
        )}
      </div>

      {/* Table */}
      <div className="bg-gray-900/50 border border-gray-800/50 rounded-xl overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-800/50 text-gray-600">
              <th className="px-3 py-2 text-left font-medium">Agent</th>
              <th className="px-3 py-2 text-left font-medium">Time</th>
              <th className="px-3 py-2 text-right font-medium">Duration</th>
              <th className="px-3 py-2 text-right font-medium">Cost</th>
              <th className="px-3 py-2 text-right font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call, i) => {
              const badge = STATUS_BADGES[call.status] || STATUS_BADGES.running;
              const agentColor = AGENT_COLORS[call.agent] || 'text-gray-400';
              return (
                <tr key={i} className="border-b border-gray-800/30 hover:bg-gray-800/20">
                  <td className={`px-3 py-2 font-medium capitalize ${agentColor}`}>
                    {call.agent}
                  </td>
                  <td className="px-3 py-2 text-gray-500 font-mono">
                    {formatTime(call.startTime)}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-400 font-mono">
                    {formatDuration(call.startTime, call.endTime)}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-400 font-mono">
                    {call.cost !== undefined ? `$${call.cost.toFixed(4)}` : '-'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium
                      ${badge.bg} ${badge.text}`}>
                      {badge.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
