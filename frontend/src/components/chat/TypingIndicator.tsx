import type { TypingUser } from '../../types';

interface TypingIndicatorProps {
  users: TypingUser[];
}

export default function TypingIndicator({ users }: TypingIndicatorProps): JSX.Element | null {
  if (users.length === 0) return null;

  const text =
    users.length === 1
      ? `${users[0].user_id} is typing`
      : users.length === 2
        ? `${users[0].user_id} and ${users[1].user_id} are typing`
        : `${users[0].user_id} and ${users.length - 1} others are typing`;

  return (
    <div
      className="flex items-center gap-2 px-4 py-1.5 text-xs"
      style={{ color: 'var(--text-muted)' }}
      role="status"
      aria-live="polite"
      aria-label={text}
    >
      {/* Animated dots */}
      <span className="flex gap-0.5" aria-hidden="true">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="w-1.5 h-1.5 rounded-full"
            style={{
              background: 'var(--accent-blue)',
              animation: `typingDot 1.4s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </span>
      <span className="truncate">{text}</span>
      <style>{`
        @keyframes typingDot {
          0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
          30% { opacity: 1; transform: translateY(-3px); }
        }
      `}</style>
    </div>
  );
}
