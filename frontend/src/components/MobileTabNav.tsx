import React from 'react';
import type { MobileView } from '../reducers/projectReducer';
import type { Project } from '../types';

// ============================================================================
// Tab Item Type
// ============================================================================

interface MobileNavItem {
  id: MobileView;
  icon: React.ReactElement;
  label: string;
}

// ============================================================================
// Props Interface
// ============================================================================

export interface MobileTabNavProps {
  mobileView: MobileView;
  onSetMobileView: (view: MobileView) => void;
  projectStatus: Project['status'];
  activitiesCount: number;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onShowClearConfirm: () => void;
  lastTicker: string;
  message: string;
  onMessageChange: (value: string) => void;
  sending: boolean;
  onSend: (msg: string) => void;
}

// ============================================================================
// Static tab definitions (defined outside component to avoid re-creation)
// ============================================================================

const MOBILE_NAV_ITEMS: MobileNavItem[] = [
  {
    id: 'orchestra',
    label: 'Nexus',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>,
  },
  {
    id: 'activity',
    label: 'Log',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>,
  },
  {
    id: 'plan',
    label: 'Plan',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>,
  },
  {
    id: 'code',
    label: 'Code',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
  },
  {
    id: 'changes',
    label: 'Diff',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 3v18M3 12h18"/></svg>,
  },
  {
    id: 'trace',
    label: 'Trace',
    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,
  },
];

// ============================================================================
// MobileTabNav Component
// ============================================================================

/** Mobile bottom navigation bar with tab buttons, action controls, and message input. */
const MobileTabNav = React.memo(function MobileTabNav({
  mobileView,
  onSetMobileView,
  projectStatus,
  activitiesCount,
  onPause,
  onResume,
  onStop,
  onShowClearConfirm,
  lastTicker,
  message,
  onMessageChange,
  sending,
  onSend,
}: MobileTabNavProps): React.ReactElement {
  const handleSubmit = (): void => {
    if (message.trim() && !sending) {
      const msg = message.trim();
      onMessageChange('');
      onSend(msg);
    }
  };

  return (
    <div
      className="flex-shrink-0"
      style={{ borderTop: '1px solid var(--border-dim)', background: 'var(--bg-panel)', backdropFilter: 'blur(12px)', touchAction: 'none' }}
    >
      {/* Live ticker */}
      {lastTicker && (
        <div className="px-3 pt-1.5 pb-0.5">
          <div className="text-[10px] truncate"
            style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)', opacity: 0.7 }}>
            {lastTicker}
          </div>
        </div>
      )}

      {/* Tab nav (icon-only, tight) */}
      <div className="flex items-center px-1">
        {MOBILE_NAV_ITEMS.map(item => (
          <button
            key={item.id}
            onClick={() => {
              onSetMobileView(item.id);
              // Haptic feedback on tab switch
              if ('vibrate' in navigator) {
                navigator.vibrate(8);
              }
            }}
            className="flex-1 flex flex-col items-center justify-center py-1.5 transition-colors"
            style={{ color: mobileView === item.id ? 'var(--accent-blue)' : 'var(--text-muted)' }}
            aria-label={item.label}
            aria-current={mobileView === item.id ? 'page' : undefined}
          >
            {item.icon}
            <span className="text-[9px] mt-0.5">{item.label}</span>
            {/* Active tab indicator dot */}
            {mobileView === item.id && (
              <div className="w-1 h-1 rounded-full mt-0.5"
                style={{ background: 'var(--accent-blue)', boxShadow: '0 0 4px var(--glow-blue)' }} />
            )}
          </button>
        ))}

        {/* Inline action buttons */}
        {(projectStatus === 'running' || projectStatus === 'paused') && (
          <div className="flex items-center gap-0.5 pl-1 ml-1" style={{ borderLeft: '1px solid var(--border-dim)' }}>
            {projectStatus === 'running' && (
              <button onClick={onPause} className="p-1.5" style={{ color: 'var(--accent-amber)' }} aria-label="Pause project">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <rect x="4" y="3" width="3" height="10" rx="0.5"/>
                  <rect x="9" y="3" width="3" height="10" rx="0.5"/>
                </svg>
              </button>
            )}
            {projectStatus === 'paused' && (
              <button onClick={onResume} className="p-1.5" style={{ color: 'var(--accent-green)' }} aria-label="Resume project">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M4 3l9 5-9 5V3z"/>
                </svg>
              </button>
            )}
            <button onClick={onStop} className="p-1.5" style={{ color: 'var(--accent-red)' }} aria-label="Stop project">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <rect x="3" y="3" width="10" height="10" rx="1"/>
              </svg>
            </button>
          </div>
        )}
        {/* Clear history button — visible when idle */}
        {projectStatus === 'idle' && activitiesCount > 0 && (
          <button onClick={onShowClearConfirm} className="p-1.5 ml-1" style={{ color: 'var(--text-muted)' }}
            title="Clear history" aria-label="Clear history">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4"/>
            </svg>
          </button>
        )}
      </div>

      {/* Input row (compact) */}
      <div className="flex items-center gap-1.5 px-2 pt-1" style={{ paddingBottom: 'max(8px, env(safe-area-inset-bottom, 8px))' }}>
        <input
          type="text"
          value={message}
          onChange={(e) => onMessageChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleSubmit();
            }
          }}
          disabled={sending}
          placeholder={projectStatus === 'idle' ? 'Send a task...' : 'Message...'}
          className="flex-1 text-base rounded-full px-4 py-2 focus:outline-none min-w-0 disabled:opacity-50 transition-colors focus:ring-2 focus:ring-[var(--accent-blue)]"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)',
          }}
          aria-label="Message input"
        />
        <button
          onClick={handleSubmit}
          disabled={!message.trim() || sending}
          className="p-2 rounded-full transition-all flex-shrink-0 focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
          style={{
            background: message.trim() && !sending ? 'var(--accent-blue)' : 'var(--bg-elevated)',
            color: message.trim() && !sending ? 'white' : 'var(--text-muted)',
            boxShadow: message.trim() && !sending ? '0 0 12px var(--glow-blue)' : 'none',
          }}
          aria-label="Send message"
        >
          {sending ? (
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M14 2L7 9M14 2l-5 12-2-5-5-2 12-5z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
});

export default MobileTabNav;
