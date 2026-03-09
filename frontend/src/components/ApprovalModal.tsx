import { useState, useEffect, useRef } from 'react';

interface Props {
  description: string;
  projectId: string;
  onClose: () => void;
}

export default function ApprovalModal({ description, projectId, onClose }: Props) {
  const [loading, setLoading] = useState<'approve' | 'reject' | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Focus trap + keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Enter' && !e.shiftKey && !loading) handleApprove();
    };
    document.addEventListener('keydown', handler);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', handler);
  }, [loading]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleApprove = async () => {
    if (loading) return;
    setLoading('approve');
    try {
      const res = await fetch(`/api/projects/${projectId}/approve`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onClose();
    } catch (e) {
      setLoading(null);
      // Show inline error instead of alert
      console.error('Failed to approve:', e);
    }
  };

  const handleReject = async () => {
    if (loading) return;
    setLoading('reject');
    try {
      const res = await fetch(`/api/projects/${projectId}/reject`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onClose();
    } catch (e) {
      setLoading(null);
      console.error('Failed to reject:', e);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(8px)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Approval required"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="rounded-2xl p-6 max-w-md w-full animate-[fadeSlideIn_0.2s_ease-out] focus:outline-none"
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border-subtle)',
          boxShadow: '0 25px 50px -12px rgba(0,0,0,0.5), 0 0 40px rgba(245,166,35,0.08)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Amber accent stripe */}
        <div className="h-1 -mx-6 -mt-6 mb-5 rounded-t-2xl"
          style={{ background: 'linear-gradient(90deg, var(--accent-amber), var(--accent-red))' }} />

        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full flex items-center justify-center text-xl"
            style={{ background: 'rgba(245,166,35,0.12)' }}>
            🛑
          </div>
          <div>
            <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
              Approval Required
            </h3>
            <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              The orchestrator needs your permission to proceed
            </p>
          </div>
        </div>

        {/* Description */}
        <div className="rounded-xl px-4 py-3 mb-5"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-dim)',
          }}>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            {description}
          </p>
        </div>

        {/* Keyboard hint */}
        <p className="text-[10px] mb-3 text-center" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Press <kbd className="px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>Enter</kbd> to approve
          {' · '}
          <kbd className="px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>Esc</kbd> to dismiss
        </p>

        {/* Buttons */}
        <div className="flex gap-3">
          <button
            onClick={handleReject}
            disabled={loading !== null}
            className="flex-1 px-4 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 active:scale-[0.97] disabled:opacity-50"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-subtle)',
            }}
            onMouseEnter={e => { if (!loading) { e.currentTarget.style.borderColor = 'rgba(245,71,91,0.3)'; e.currentTarget.style.color = 'var(--accent-red)'; } }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
          >
            {loading === 'reject' ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
                </svg>
                Rejecting...
              </span>
            ) : 'Reject'}
          </button>
          <button
            onClick={handleApprove}
            disabled={loading !== null}
            className="flex-1 px-4 py-2.5 text-sm font-semibold rounded-xl transition-all duration-200 active:scale-[0.97] disabled:opacity-50"
            style={{
              background: 'linear-gradient(135deg, var(--accent-green), #2ba86e)',
              color: 'white',
              boxShadow: '0 4px 15px rgba(61,214,140,0.25), inset 0 1px 0 rgba(255,255,255,0.1)',
            }}
          >
            {loading === 'approve' ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
                </svg>
                Approving...
              </span>
            ) : 'Approve'}
          </button>
        </div>
      </div>
    </div>
  );
}
