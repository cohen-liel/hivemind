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
  developer: 'var(--accent-blue)',
  reviewer: 'var(--accent-purple)',
  tester: 'var(--accent-amber)',
  devops: 'var(--accent-cyan)',
  orchestrator: 'var(--text-secondary)',
};

const STATUS_CONFIG: Record<string, { bg: string; color: string; label: string }> = {
  running: { bg: 'var(--glow-blue)', color: 'var(--accent-blue)', label: 'Running' },
  done: { bg: 'var(--glow-green)', color: 'var(--accent-green)', label: 'Done' },
  error: { bg: 'var(--glow-red)', color: 'var(--accent-red)', label: 'Error' },
};

export default function NetworkTrace({ calls }: Props) {
  if (calls.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
          📡
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No API calls yet</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
          SDK calls will appear here as agents work
        </p>
      </div>
    );
  }

  const totalCost = calls.reduce((sum, c) => sum + (c.cost || 0), 0);

  return (
    <div className="p-4">
      {/* Summary bar */}
      <div className="flex items-center gap-4 mb-3">
        <span className="text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          API Trace
        </span>
        <span className="text-xs px-2 py-0.5 rounded-md"
          style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
          {calls.length} calls
        </span>
        {totalCost > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-md"
            style={{ background: 'var(--glow-green)', color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>
            ${totalCost.toFixed(4)}
          </span>
        )}
      </div>

      {/* Table */}
      <div className="rounded-xl overflow-hidden"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-dim)' }}>
              <th className="px-3 py-2.5 text-left font-medium"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Agent</th>
              <th className="px-3 py-2.5 text-left font-medium"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Time</th>
              <th className="px-3 py-2.5 text-right font-medium"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Duration</th>
              <th className="px-3 py-2.5 text-right font-medium"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Cost</th>
              <th className="px-3 py-2.5 text-right font-medium"
                style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call, i) => {
              const badge = STATUS_CONFIG[call.status] || STATUS_CONFIG.running;
              const agentColor = AGENT_COLORS[call.agent] || 'var(--text-secondary)';
              return (
                <tr
                  key={i}
                  className="transition-colors"
                  style={{ borderBottom: i < calls.length - 1 ? '1px solid var(--border-dim)' : 'none' }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <td className="px-3 py-2.5 font-medium capitalize" style={{ color: agentColor }}>
                    {call.agent}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {formatTime(call.startTime)}
                  </td>
                  <td className="px-3 py-2.5 text-right" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                    {formatDuration(call.startTime, call.endTime)}
                  </td>
                  <td className="px-3 py-2.5 text-right" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                    {call.cost !== undefined ? `$${call.cost.toFixed(4)}` : '-'}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span
                      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold"
                      style={{
                        background: badge.bg,
                        color: badge.color,
                        border: `1px solid ${badge.color}20`,
                      }}
                    >
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
