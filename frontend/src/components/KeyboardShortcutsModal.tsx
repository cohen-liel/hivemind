import { useEffect } from 'react';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { keys: ['⌘', 'N'], description: 'New project' },
  { keys: ['⌘', ','], description: 'Settings' },
  { keys: ['⌘', '1'], description: 'Dashboard' },
  { keys: ['⌘', '2'], description: 'Schedules' },
  { keys: ['Esc'], description: 'Back to dashboard' },
  { keys: ['?'], description: 'This menu' },
  { keys: ['Enter'], description: 'Send message' },
  { keys: ['Shift', 'Enter'], description: 'New line in message' },
];

export default function KeyboardShortcutsModal({ isOpen, onClose }: Props) {
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 animate-[fadeSlideIn_0.15s_ease-out]"
        style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      />

      {/* Modal */}
      <div
        className="relative w-full max-w-sm rounded-2xl overflow-hidden animate-[slideUp_0.25s_ease-out]"
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border-subtle)',
          boxShadow: '0 25px 50px rgba(0,0,0,0.5)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="px-5 py-4 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--border-dim)' }}
        >
          <div className="flex items-center gap-2.5">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
              style={{ background: 'var(--glow-blue)' }}
            >
              ⌨️
            </div>
            <h2
              className="text-sm font-bold"
              style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
            >
              Keyboard Shortcuts
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>

        {/* Shortcuts list */}
        <div className="px-5 py-3 space-y-1">
          {SHORTCUTS.map((shortcut, i) => (
            <div
              key={i}
              className="flex items-center justify-between py-2 px-1 rounded-lg"
              style={{
                animation: `fadeSlideIn 0.2s ease-out ${i * 30}ms backwards`,
              }}
            >
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {shortcut.description}
              </span>
              <div className="flex items-center gap-1">
                {shortcut.keys.map((key, ki) => (
                  <kbd
                    key={ki}
                    className="min-w-[24px] h-6 px-1.5 flex items-center justify-center rounded-md text-[11px] font-mono font-medium"
                    style={{
                      background: 'var(--bg-elevated)',
                      border: '1px solid var(--border-subtle)',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-mono)',
                      boxShadow: '0 1px 2px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04)',
                    }}
                  >
                    {key}
                  </kbd>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer hint */}
        <div
          className="px-5 py-3 text-center"
          style={{ borderTop: '1px solid var(--border-dim)' }}
        >
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Press <kbd className="px-1 py-0.5 rounded text-[9px] font-mono" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>?</kbd> or <kbd className="px-1 py-0.5 rounded text-[9px] font-mono" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>Esc</kbd> to close
          </span>
        </div>
      </div>
    </div>
  );
}
