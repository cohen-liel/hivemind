import { useEffect, useRef, useState } from 'react';
import { useWSSubscribe } from '../WebSocketContext';

/**
 * A full-width sticky banner shown at the top of the viewport when the
 * WebSocket connection is lost. Transitions through three visual states:
 *
 *  connected     → hidden (no render)
 *  disconnected  → amber "Reconnecting…" banner with spinner
 *  reconnected   → brief green "Reconnected" flash, then hides
 *
 * The banner is always rendered in the DOM when visible so that screen
 * reader live-regions announce the status change immediately.
 */
export default function WSReconnectBanner(): React.ReactElement | null {
  const { connected } = useWSSubscribe(() => {});
  const prevRef = useRef<boolean | null>(null);
  const [visible, setVisible] = useState(false);
  const [phase, setPhase] = useState<'reconnecting' | 'restored'>('reconnecting');
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (prevRef.current === null) {
      prevRef.current = connected;
      return;
    }

    if (!connected && prevRef.current) {
      // Just disconnected
      if (hideTimerRef.current) {
        clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
      setPhase('reconnecting');
      setVisible(true);
    } else if (connected && !prevRef.current) {
      // Just reconnected
      setPhase('restored');
      // Auto-hide after 2.5 s
      hideTimerRef.current = setTimeout(() => {
        setVisible(false);
      }, 2500);
    }

    prevRef.current = connected;

    return () => {
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    };
  }, [connected]);

  if (!visible) return null;

  const isReconnecting = phase === 'reconnecting';

  return (
    <div
      role="alert"
      aria-live="assertive"
      aria-atomic="true"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        paddingTop: '8px',
        paddingBottom: '8px',
        paddingLeft: '16px',
        paddingRight: '16px',
        fontSize: '13px',
        fontWeight: 600,
        fontFamily: 'var(--font-display)',
        background: isReconnecting
          ? 'rgba(245, 166, 35, 0.95)'
          : 'rgba(61, 214, 140, 0.95)',
        color: isReconnecting ? '#1a1200' : '#003320',
        backdropFilter: 'blur(8px)',
        boxShadow: isReconnecting
          ? '0 2px 16px rgba(245, 166, 35, 0.4)'
          : '0 2px 16px rgba(61, 214, 140, 0.4)',
        animation: 'slideDownBanner 0.25s ease-out',
        transition: 'background 0.3s ease, color 0.3s ease, box-shadow 0.3s ease',
      }}
    >
      {isReconnecting ? (
        <>
          {/* Spinner */}
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            aria-hidden="true"
            style={{ animation: 'spin 0.8s linear infinite', flexShrink: 0 }}
          >
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
          </svg>
          <span>Reconnecting — updates will resume shortly</span>
        </>
      ) : (
        <>
          {/* Check mark */}
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            style={{ flexShrink: 0 }}
          >
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <span>Connected — you're up to date</span>
        </>
      )}
    </div>
  );
}
