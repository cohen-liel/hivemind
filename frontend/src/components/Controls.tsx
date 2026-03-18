import { useState, useRef, useEffect, useCallback } from 'react';
import { AGENT_ICONS } from '../constants';

type AgentMode = 'autonomous' | 'interactive';

interface Props {
  status: string;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onSend: (message: string, mode?: string) => void;
}

export default function Controls({ status, onPause, onResume, onStop, onSend }: Props): React.ReactElement {
  const [message, setMessage] = useState('');
  const [mode, setMode] = useState<AgentMode>('autonomous');
  const targetAgent = 'orchestrator';
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-resize textarea (grows up to 5 lines, then scrolls)
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    // Cap at 5 lines: font-size 15px × line-height 1.625 ≈ 24.4px per line
    const lineHeightPx = parseFloat(getComputedStyle(el).lineHeight) || 24.4;
    const maxH = 5 * lineHeightPx;
    el.style.height = Math.min(el.scrollHeight, maxH) + 'px';
  }, [message]);

  // iOS keyboard viewport fix: when the textarea is focused, the virtual keyboard
  // may cover it. Use the visualViewport API to adjust position on iOS Safari.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;

    const adjustForKeyboard = (): void => {
      const container = containerRef.current;
      if (!container) return;

      // Calculate the keyboard offset: the difference between layout viewport and visual viewport
      const keyboardOffset = window.innerHeight - vv.height - vv.offsetTop;

      if (keyboardOffset > 50) {
        // Keyboard is open — translate the controls up so they sit above the keyboard
        container.style.transform = `translateY(-${keyboardOffset}px)`;
      } else {
        // Keyboard is closed
        container.style.transform = 'translateY(0)';
      }
    };

    // Only attach listeners when focused to avoid unnecessary work
    if (focused) {
      adjustForKeyboard();
      vv.addEventListener('resize', adjustForKeyboard);
      vv.addEventListener('scroll', adjustForKeyboard);
    }

    return () => {
      vv.removeEventListener('resize', adjustForKeyboard);
      vv.removeEventListener('scroll', adjustForKeyboard);
      // Reset transform when cleaning up
      if (containerRef.current) {
        containerRef.current.style.transform = 'translateY(0)';
      }
    };
  }, [focused]);

  const handleFocus = useCallback((): void => {
    setFocused(true);
    // On iOS, scroll to ensure the input is visible after keyboard animation
    setTimeout(() => {
      textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }, 300);
  }, []);

  const handleBlur = useCallback((): void => {
    setFocused(false);
    // Reset any viewport adjustments
    if (containerRef.current) {
      containerRef.current.style.transform = 'translateY(0)';
    }
    // Reset iOS scroll offset after keyboard closes
    setTimeout(() => {
      window.scrollTo(0, 0);
    }, 100);
  }, []);

  const handleSend = async (): Promise<void> => {
    if (!message.trim() || sending) return;
    setSending(true);
    setSendError('');
    try {
      await onSend(message.trim(), mode);
      setMessage('');
      // Scroll the activity feed to bottom after sending so the user sees their message
      requestAnimationFrame(() => {
        const feedEl = document.getElementById('activity-scroll-container');
        if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
      });
    } catch {
      setSendError('Failed to send — tap to retry');
    } finally {
      setTimeout(() => setSending(false), 1000);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isActive = status === 'running' || status === 'paused';
  const hasContent = message.trim().length > 0;
  const targetIcon = AGENT_ICONS[targetAgent] || '🎯';

  return (
    <div
      ref={containerRef}
      className="sticky bottom-0 z-20 safe-area-bottom transition-all duration-300 overflow-hidden"
      style={{
        background: 'var(--bg-panel)',
        borderTop: focused ? '1px solid var(--border-active)' : '1px solid var(--border-dim)',
        boxShadow: focused ? '0 -8px 30px rgba(0,0,0,0.3)' : '0 -4px 12px rgba(0,0,0,0.15)',
        willChange: focused ? 'transform' : 'auto',
      }}
    >
      {/* Control buttons — pill-style */}
      {isActive && (
        <div className="flex items-center gap-2 px-4 pt-3 pb-1">
          {status === 'running' && (
            <button
              onClick={onPause}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all duration-200 active:scale-95 focus:outline-none focus-visible:ring-2"
              style={{
                background: 'rgba(245,166,35,0.1)',
                color: 'var(--accent-amber)',
                border: '1px solid rgba(245,166,35,0.15)',
              }}
              aria-label="Pause project"
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,166,35,0.18)'; e.currentTarget.style.borderColor = 'rgba(245,166,35,0.3)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'rgba(245,166,35,0.1)'; e.currentTarget.style.borderColor = 'rgba(245,166,35,0.15)'; }}
            >
              <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><rect x="4" y="3" width="3" height="10" rx="0.5"/><rect x="9" y="3" width="3" height="10" rx="0.5"/></svg>
              Pause
            </button>
          )}
          {status === 'paused' && (
            <button
              onClick={onResume}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all duration-200 active:scale-95 focus:outline-none focus-visible:ring-2"
              style={{
                background: 'rgba(61,214,140,0.1)',
                color: 'var(--accent-green)',
                border: '1px solid rgba(61,214,140,0.15)',
              }}
              aria-label="Resume project"
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(61,214,140,0.18)'; e.currentTarget.style.borderColor = 'rgba(61,214,140,0.3)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'rgba(61,214,140,0.1)'; e.currentTarget.style.borderColor = 'rgba(61,214,140,0.15)'; }}
            >
              <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M4 3l9 5-9 5V3z"/></svg>
              Resume
            </button>
          )}
          <button
            onClick={onStop}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all duration-200 active:scale-95 focus:outline-none focus-visible:ring-2"
            style={{
              background: 'rgba(245,71,91,0.08)',
              color: 'var(--accent-red)',
              border: '1px solid rgba(245,71,91,0.12)',
            }}
            aria-label="Stop project"
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,71,91,0.15)'; e.currentTarget.style.borderColor = 'rgba(245,71,91,0.25)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(245,71,91,0.08)'; e.currentTarget.style.borderColor = 'rgba(245,71,91,0.12)'; }}
          >
            <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="3" width="10" height="10" rx="1.5"/></svg>
            Stop
          </button>

          {/* Spacer + status hint */}
          <div className="flex-1" />
          {status === 'running' && (
            <span className="flex items-center gap-1.5 text-[10px]" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
              RUNNING
            </span>
          )}
          {status === 'paused' && (
            <span className="text-[10px]" style={{ color: 'var(--accent-amber)', fontFamily: 'var(--font-mono)' }}>
              PAUSED
            </span>
          )}
        </div>
      )}

      {/* Input row — premium feel */}
      <div className="flex items-end gap-2.5 px-3 py-3">
        {/* Orchestrator indicator — all messages go through the conductor */}
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center text-base flex-shrink-0"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-subtle)',
          }}
          title="All messages go through the Orchestrator"
          aria-hidden="true"
        >
          {targetIcon}
        </div>

        {/* Text input */}
        <div
          className="flex-1 min-w-0 rounded-2xl transition-all duration-300 overflow-hidden"
          style={{
            background: 'var(--bg-elevated)',
            border: sendError ? '1px solid var(--accent-red)' : focused ? '1px solid var(--border-active)' : '1px solid var(--border-subtle)',
            boxShadow: sendError ? '0 0 0 3px rgba(245,71,91,0.12)' : focused ? '0 0 0 3px rgba(99,140,255,0.08)' : 'none',
          }}
        >
          {sendError && (
            <div className="px-4 pt-2 text-[11px] font-medium" style={{ color: 'var(--accent-red)' }}>
              ⚠️ {sendError}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => { setMessage(e.target.value); if (sendError) setSendError(''); }}
            onKeyDown={handleKeyDown}
            onFocus={handleFocus}
            onBlur={handleBlur}
            placeholder={status === 'idle' ? 'Describe a task for your agents…' : 'Send a message…'}
            rows={1}
            className="w-full px-4 py-2.5 text-[15px] resize-none focus:outline-none bg-transparent leading-relaxed"
            style={{ color: 'var(--text-primary)', maxHeight: '122px' }}
            aria-label="Message input"
          />
        </div>

        {/* Mode toggle — Plan (interactive) vs Execute (autonomous) */}
        <div
          className="flex items-center rounded-lg overflow-hidden flex-shrink-0"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-subtle)',
            height: '40px',
          }}
          role="radiogroup"
          aria-label="Execution mode"
        >
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'interactive'}
            onClick={() => setMode('interactive')}
            className="px-2.5 h-full text-[11px] font-semibold transition-all duration-200"
            style={{
              background: mode === 'interactive' ? 'rgba(167,139,250,0.15)' : 'transparent',
              color: mode === 'interactive' ? 'var(--accent-purple, #a78bfa)' : 'var(--text-muted)',
              borderRight: '1px solid var(--border-subtle)',
            }}
            title="Plan first, then execute after approval"
          >
            Plan
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'autonomous'}
            onClick={() => setMode('autonomous')}
            className="px-2.5 h-full text-[11px] font-semibold transition-all duration-200"
            style={{
              background: mode === 'autonomous' ? 'rgba(61,214,140,0.12)' : 'transparent',
              color: mode === 'autonomous' ? 'var(--accent-green)' : 'var(--text-muted)',
            }}
            title="Execute immediately without plan approval"
          >
            Execute
          </button>
        </div>

        {/* Send button — grows when active */}
        <button
          onClick={handleSend}
          disabled={!hasContent || sending}
          className="flex-shrink-0 rounded-xl transition-all duration-300 flex items-center justify-center active:scale-90 focus:outline-none focus-visible:ring-2"
          style={{
            width: hasContent && !sending ? '44px' : '40px',
            height: hasContent && !sending ? '44px' : '40px',
            background: hasContent && !sending
              ? 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)'
              : 'var(--bg-elevated)',
            color: hasContent && !sending ? 'white' : 'var(--text-muted)',
            boxShadow: hasContent && !sending
              ? '0 4px 15px rgba(99,140,255,0.35), inset 0 1px 0 rgba(255,255,255,0.1)'
              : 'none',
            border: hasContent && !sending ? 'none' : '1px solid var(--border-subtle)',
            cursor: hasContent && !sending ? 'pointer' : 'default',
          }}
          aria-label="Send message"
        >
          {sending ? (
            <svg className="w-5 h-5 animate-spin" viewBox="0 0 20 20" fill="none">
              <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="2" strokeDasharray="36" strokeDashoffset="10" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
              <path d="M17.5 2.5L9 11M17.5 2.5l-6 15-2.5-6.5L2.5 8.5l15-6z"
                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}
