// ============================================================
// GRACEFUL ERROR STATE COMPONENT
// ============================================================

interface Props {
  title?: string;
  message?: string;
  icon?: string;
  onRetry?: () => void;
  variant?: 'connection' | 'notfound' | 'error' | 'empty';
}

const VARIANTS = {
  connection: {
    icon: '🔌',
    title: 'Connection Lost',
    message: 'Unable to reach the server. Make sure the backend is running and try again.',
  },
  notfound: {
    icon: '🔍',
    title: 'Not Found',
    message: 'The resource you are looking for does not exist or has been removed.',
  },
  error: {
    icon: '⚠️',
    title: 'Something Went Wrong',
    message: 'An unexpected error occurred. Please try again.',
  },
  empty: {
    icon: '📭',
    title: 'Nothing Here Yet',
    message: 'Get started by creating your first item.',
  },
} as const;

export default function ErrorState({
  title,
  message,
  icon,
  onRetry,
  variant = 'error',
}: Props) {
  const defaults = VARIANTS[variant];

  const displayTitle = title ?? defaults.title;
  const displayMessage = message ?? defaults.message;
  const displayIcon = icon ?? defaults.icon;

  return (
    <div
      className="flex flex-col items-center justify-center py-16 px-6 page-enter"
      role="status"
      aria-label={displayTitle}
    >
      {/* Icon — decorative only */}
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl mb-4"
        aria-hidden="true"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-dim)',
          boxShadow: '0 4px 20px rgba(0,0,0,0.2)',
        }}
      >
        {displayIcon}
      </div>

      {/* Title */}
      <h3
        className="text-lg font-semibold mb-1.5"
        style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
      >
        {displayTitle}
      </h3>

      {/* Message */}
      <p
        className="text-sm text-center max-w-sm leading-relaxed"
        style={{ color: 'var(--text-muted)' }}
      >
        {displayMessage}
      </p>

      {/* Retry button */}
      {onRetry && (
        <button
          onClick={onRetry}
          aria-label="Retry"
          className="mt-5 px-5 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 active:scale-95"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-subtle)',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background = 'var(--glow-blue)';
            e.currentTarget.style.borderColor = 'rgba(99,140,255,0.25)';
            e.currentTarget.style.color = 'var(--accent-blue)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'var(--bg-elevated)';
            e.currentTarget.style.borderColor = 'var(--border-subtle)';
            e.currentTarget.style.color = 'var(--text-primary)';
          }}
        >
          Try Again
        </button>
      )}
    </div>
  );
}
