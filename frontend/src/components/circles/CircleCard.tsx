import type { Circle } from '../../types';

interface CircleCardProps {
  circle: Circle;
  isActive: boolean;
  onClick: () => void;
}

export default function CircleCard({ circle, isActive, onClick }: CircleCardProps): JSX.Element {
  const initials = circle.name
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() ?? '')
    .join('');

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-2xl p-4 transition-all duration-200 interactive-card group ${
        isActive ? 'ring-1' : ''
      }`}
      style={{
        background: isActive ? 'var(--bg-elevated)' : 'var(--bg-card)',
        border: `1px solid ${isActive ? 'var(--border-active)' : 'var(--border-dim)'}`,
        boxShadow: isActive ? '0 0 20px var(--glow-blue)' : 'none',
      }}
      aria-label={`Select circle: ${circle.name}`}
      aria-current={isActive ? 'true' : undefined}
    >
      <div className="flex items-start gap-3">
        {/* Avatar */}
        {circle.avatar_url ? (
          <img
            src={circle.avatar_url}
            alt=""
            className="w-10 h-10 rounded-xl object-cover flex-shrink-0"
          />
        ) : (
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 text-sm font-bold"
            style={{
              background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-blue))',
              color: 'white',
            }}
          >
            {initials}
          </div>
        )}

        <div className="flex-1 min-w-0">
          <h3
            className="text-sm font-semibold truncate"
            style={{ color: 'var(--text-primary)' }}
          >
            {circle.name}
          </h3>
          {circle.description && (
            <p
              className="text-xs mt-0.5 line-clamp-2"
              style={{ color: 'var(--text-muted)' }}
            >
              {circle.description}
            </p>
          )}
          <div className="flex items-center gap-3 mt-2">
            <span
              className="text-[11px] flex items-center gap-1"
              style={{ color: 'var(--text-muted)' }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="6" cy="5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
                <path d="M1 14c0-2.5 2-4.5 5-4.5s5 2 5 4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                <circle cx="11" cy="6" r="2" stroke="currentColor" strokeWidth="1.3" />
                <path d="M12 14c1.5-.5 3-2 3-3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
              {circle.member_count} member{circle.member_count !== 1 ? 's' : ''}
            </span>
            <span
              className="text-[11px] flex items-center gap-1"
              style={{ color: 'var(--text-muted)' }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <path d="M9 4.5H7.5V7.5H4.5V9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
              {circle.project_count} project{circle.project_count !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}
