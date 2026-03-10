import { useState, useEffect, useCallback, createContext, useContext } from 'react';

// ============================================================
// TOAST NOTIFICATION SYSTEM
// ============================================================

type ToastType = 'success' | 'error' | 'warning' | 'info';

interface Toast {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}

/** Input type for creating a toast (without auto-generated id) */
type ToastInput = Omit<Toast, 'id'>;

interface ToastContextType {
  addToast: (toast: ToastInput) => void;
  removeToast: (id: string) => void;
  success: (title: string, message?: string) => void;
  error: (title: string, message?: string) => void;
  warning: (title: string, message?: string) => void;
  info: (title: string, message?: string) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}


const TOAST_ICONS: Record<ToastType, string> = {
  success: '✓',
  error: '✕',
  warning: '!',
  info: 'i',
};

const TOAST_COLORS: Record<ToastType, { bg: string; border: string; icon: string; glow: string }> = {
  success: {
    bg: 'rgba(52, 211, 153, 0.08)',
    border: 'rgba(52, 211, 153, 0.2)',
    icon: 'var(--accent-green)',
    glow: '0 4px 20px rgba(52, 211, 153, 0.1)',
  },
  error: {
    bg: 'rgba(245, 71, 91, 0.08)',
    border: 'rgba(245, 71, 91, 0.2)',
    icon: 'var(--accent-red)',
    glow: '0 4px 20px rgba(245, 71, 91, 0.1)',
  },
  warning: {
    bg: 'rgba(251, 191, 36, 0.08)',
    border: 'rgba(251, 191, 36, 0.2)',
    icon: 'var(--accent-amber)',
    glow: '0 4px 20px rgba(251, 191, 36, 0.1)',
  },
  info: {
    bg: 'rgba(99, 140, 255, 0.08)',
    border: 'rgba(99, 140, 255, 0.2)',
    icon: 'var(--accent-blue)',
    glow: '0 4px 20px rgba(99, 140, 255, 0.1)',
  },
};

const EXIT_ANIMATION_MS = 300;

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  const [exiting, setExiting] = useState(false);
  const colors = TOAST_COLORS[toast.type];

  const dismiss = useCallback(() => {
    setExiting(true);
    setTimeout(onRemove, EXIT_ANIMATION_MS);
  }, [onRemove]);

  useEffect(() => {
    const duration = Math.max(toast.duration ?? 4000, EXIT_ANIMATION_MS + 100);
    const exitTimer = setTimeout(() => setExiting(true), duration - EXIT_ANIMATION_MS);
    const removeTimer = setTimeout(onRemove, duration);
    return () => {
      clearTimeout(exitTimer);
      clearTimeout(removeTimer);
    };
  }, [toast.duration, onRemove]);

  return (
    <div
      role={toast.type === 'error' ? 'alert' : 'status'}
      aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
      aria-atomic="true"
      className={`flex items-start gap-3 px-4 py-3 rounded-xl backdrop-blur-md transition-all duration-300 ${
        exiting ? 'opacity-0 translate-x-4' : 'opacity-100 translate-x-0'
      }`}
      style={{
        background: colors.bg,
        border: `1px solid ${colors.border}`,
        boxShadow: colors.glow,
        minWidth: '280px',
        maxWidth: '400px',
        animation: 'toastSlideIn 0.3s ease-out',
      }}
    >
      {/* Icon — decorative */}
      <div
        className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5"
        aria-hidden="true"
        style={{
          background: colors.border,
          color: colors.icon,
        }}
      >
        {TOAST_ICONS[toast.type]}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          {toast.title}
        </p>
        {toast.message && (
          <p className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            {toast.message}
          </p>
        )}
      </div>

      {/* Close */}
      <button
        onClick={dismiss}
        aria-label="Dismiss notification"
        className="p-1 rounded-lg transition-colors flex-shrink-0"
        style={{ color: 'var(--text-muted)' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((toast: ToastInput) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts(prev => [...prev, { ...toast, id }].slice(-5));
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const success = useCallback((title: string, message?: string) => addToast({ type: 'success', title, message }), [addToast]);
  const error = useCallback((title: string, message?: string) => addToast({ type: 'error', title, message }), [addToast]);
  const warning = useCallback((title: string, message?: string) => addToast({ type: 'warning', title, message }), [addToast]);
  const info = useCallback((title: string, message?: string) => addToast({ type: 'info', title, message }), [addToast]);

  return (
    <ToastContext.Provider value={{ addToast, removeToast, success, error, warning, info }}>
      {children}

      {/* Toast container — fixed top-right */}
      {toasts.length > 0 && (
        <div
          className="fixed top-4 right-4 z-[9999] flex flex-col gap-2"
          style={{ pointerEvents: 'auto' }}
          aria-label="Notifications"
        >
          {toasts.map(toast => (
            <ToastItem
              key={toast.id}
              toast={toast}
              onRemove={() => removeToast(toast.id)}
            />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}
