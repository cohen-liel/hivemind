import { memo } from 'react';
import { useWatchdogHealth } from './WatchdogStatusPanel';

type HealthStatus = 'healthy' | 'failing' | 'unknown' | 'running';

const DOT_CONFIG: Record<HealthStatus, { color: string; pulse: boolean; label: string }> = {
  healthy: { color: 'var(--accent-green)', pulse: false, label: 'Tests passing' },
  failing: { color: 'var(--accent-red)', pulse: true, label: 'Tests failing' },
  running: { color: 'var(--accent-blue)', pulse: true, label: 'Tests running' },
  unknown: { color: 'var(--text-muted)', pulse: false, label: 'Test status unknown' },
};

interface SidebarHealthBadgeProps {
  collapsed: boolean;
}

function SidebarHealthBadgeInner({ collapsed }: SidebarHealthBadgeProps): JSX.Element {
  const { status, loading } = useWatchdogHealth();

  if (loading) return <></>;

  const config = DOT_CONFIG[status] || DOT_CONFIG.unknown;

  return (
    <div
      className={`flex items-center gap-2 px-3 py-2 rounded-xl transition-colors duration-200 ${collapsed ? 'justify-center' : ''}`}
      style={{ background: 'transparent' }}
      role="status"
      aria-label={config.label}
      title={config.label}
    >
      <span
        className={`w-2 h-2 rounded-full flex-shrink-0 transition-all duration-300 ${config.pulse ? 'animate-pulse' : ''}`}
        style={{
          background: config.color,
          boxShadow: config.pulse ? `0 0 6px ${config.color}` : 'none',
        }}
      />
      {!collapsed && (
        <span className="text-[11px] font-medium truncate" style={{ color: 'var(--text-muted)' }}>
          {status === 'healthy' ? 'Tests OK' : status === 'failing' ? 'Tests Fail' : status === 'running' ? 'Testing…' : 'Tests N/A'}
        </span>
      )}
    </div>
  );
}

const SidebarHealthBadge = memo(SidebarHealthBadgeInner);
export default SidebarHealthBadge;
