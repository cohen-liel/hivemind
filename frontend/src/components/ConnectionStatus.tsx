import { useWSStatus } from '../WebSocketContext';

/** Accessible connection status indicator with reconnection attempt display and replay error banner */
export function ConnectionStatus(): JSX.Element | null {
  const { connected, authenticated, reconnectAttempts, replayError, dismissReplayError } = useWSStatus();

  // Don't render anything when fully connected and no errors
  if (connected && authenticated && !replayError) {
    return null;
  }

  return (
    <>
      {/* Replay error warning banner */}
      {replayError && (
        <div
          role="alert"
          className="fixed top-0 left-0 right-0 z-50 flex items-center justify-between gap-3 px-4 py-2 text-sm bg-amber-500/90 text-white backdrop-blur-sm"
        >
          <div className="flex items-center gap-2 min-w-0">
            <svg
              className="w-4 h-4 flex-shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
              />
            </svg>
            <span className="truncate">{replayError}</span>
          </div>
          <button
            onClick={dismissReplayError}
            className="flex-shrink-0 p-1 rounded hover:bg-white/20 focus:outline-none focus:ring-2 focus:ring-white/50"
            aria-label="Dismiss warning"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* Connection status indicator */}
      {(!connected || !authenticated) && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium shadow-lg backdrop-blur-sm bg-gray-900/90 text-gray-200 border border-gray-700/50"
        >
          {/* Pulsing dot */}
          <span className="relative flex h-2.5 w-2.5">
            <span
              className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${
                reconnectAttempts > 0 ? 'animate-ping bg-amber-400' : 'bg-red-400'
              }`}
            />
            <span
              className={`relative inline-flex h-2.5 w-2.5 rounded-full ${
                reconnectAttempts > 0 ? 'bg-amber-500' : 'bg-red-500'
              }`}
            />
          </span>

          <span>
            {!connected && reconnectAttempts > 0
              ? `Reconnecting... (attempt ${reconnectAttempts})`
              : !connected
              ? 'Disconnected'
              : 'Authenticating...'}
          </span>
        </div>
      )}
    </>
  );
}
