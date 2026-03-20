import { createContext, useContext, useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import type { WSEvent, ConnectionQuality } from './types';
import { getWsConfig } from './agentRegistry';

type Subscriber = (event: WSEvent) => void;

interface WSContextValue {
  connected: boolean;
  /** Whether the WebSocket has completed first-frame authentication */
  authenticated: boolean;
  /** Connection quality indicator: connected | degraded | disconnected */
  connectionQuality: ConnectionQuality;
  subscribe: (callback: Subscriber) => () => void;
  /** Request replay of missed events for a project since a given sequence */
  requestReplay: (projectId: string, sinceSequence: number) => void;
  /** Send a message through the WebSocket, buffering if disconnected */
  sendMessage: (message: Record<string, unknown>) => void;
  /** Number of reconnection attempts since last successful connection */
  reconnectAttempts: number;
  /** Whether a replay request has failed (user-visible warning) */
  replayError: string | null;
  /** Dismiss the replay error warning */
  dismissReplayError: () => void;
}

const WSContext = createContext<WSContextValue>({
  connected: false,
  authenticated: false,
  connectionQuality: 'disconnected',
  subscribe: () => () => {},
  requestReplay: () => {},
  sendMessage: () => {},
  reconnectAttempts: 0,
  replayError: null,
  dismissReplayError: () => {},
});

// ── Auth token helpers ─────────────────────────────────────────────

/** LocalStorage key for the WebSocket / API auth token */
const AUTH_TOKEN_KEY = 'hivemind-auth-token';

/**
 * Retrieve the auth token used for the WebSocket first-frame auth protocol.
 *
 * Priority:
 *  1. localStorage (set via Settings or login flow)
 *  2. <meta name="hivemind-auth-token"> injected by backend into index.html
 *  3. empty string (backend may accept unauthenticated during migration)
 */
function getAuthToken(): string {
  try {
    const stored = localStorage.getItem(AUTH_TOKEN_KEY);
    if (stored) return stored;
  } catch {
    // localStorage unavailable (private browsing, etc.)
  }

  const meta = document.querySelector<HTMLMetaElement>('meta[name="hivemind-auth-token"]');
  if (meta?.content) return meta.content;

  return '';
}

/**
 * Persist an auth token (called from Settings or login flow).
 */
export function setAuthToken(token: string): void {
  try {
    if (token) {
      localStorage.setItem(AUTH_TOKEN_KEY, token);
    } else {
      localStorage.removeItem(AUTH_TOKEN_KEY);
    }
  } catch {
    // localStorage unavailable
  }
}

// ── Sequence tracking with gap detection ───────────────────────────

/**
 * Per-project sequence tracker.
 * Tracks the latest sequence_id seen for each project so we can request
 * only missed events on reconnect.
 */
const _projectSequences: Record<string, number> = {};

function _trackSequence(event: WSEvent): void {
  if (event.project_id && typeof event.sequence_id === 'number') {
    const current = _projectSequences[event.project_id] ?? 0;
    const incoming = event.sequence_id;

    if (incoming > current) {
      // Gap detection: if we skip sequence numbers, log a warning
      if (current > 0 && incoming > current + 1) {
        const gapSize = incoming - current - 1;
        console.warn(
          `[WS] Sequence gap detected for project ${event.project_id}: ` +
          `expected ${current + 1}, got ${incoming} (${gapSize} event(s) missing)`
        );
      }
      _projectSequences[event.project_id] = incoming;
    }
  }
}

// ── Heartbeat constants ────────────────────────────────────────────

/** Interval between heartbeat pings sent to the server (ms) */
const HEARTBEAT_INTERVAL_MS = 30_000;
/** If no pong received within this window, mark connection as degraded (ms) */
const HEARTBEAT_DEGRADED_THRESHOLD_MS = 45_000;
/** If no pong received within this window, consider connection stale and reconnect (ms) */
const HEARTBEAT_STALE_THRESHOLD_MS = 60_000;

// ── Priority-based message queue ───────────────────────────────────

/** Event types that are considered critical and should be sent first during queue flush */
const CRITICAL_MESSAGE_TYPES = new Set([
  'task_graph', 'plan_delta', 'execution_error', 'task_error',
  'replay', 'replay_range',
]);

interface QueuedMessage {
  payload: string;
  timestamp: number;
  priority: 'critical' | 'normal';
}

/** Max age for buffered messages (60s) — discard stale messages on flush */
const MESSAGE_MAX_AGE_MS = 60_000;
/** Max buffered messages to prevent unbounded memory growth */
const MESSAGE_QUEUE_MAX_SIZE = 100;

/**
 * Determine message priority based on its type field.
 * Critical messages are flushed first after reconnect.
 */
function classifyMessagePriority(payload: string): 'critical' | 'normal' {
  try {
    const parsed = JSON.parse(payload) as { type?: string };
    if (parsed.type && CRITICAL_MESSAGE_TYPES.has(parsed.type)) {
      return 'critical';
    }
  } catch {
    // Unparseable — treat as normal
  }
  return 'normal';
}

// ── Reconnect state sync ───────────────────────────────────────────

/**
 * After a WebSocket reconnect, fetch live state for ALL projects
 * and request event replay for any missed events using replay_range.
 *
 * Returns an error string if replay requests fail, null otherwise.
 */
async function _syncStateOnReconnect(
  subscribers: Set<Subscriber>,
  ws: WebSocket | null,
): Promise<string | null> {
  let replayError: string | null = null;

  try {
    const res = await fetch('/api/projects');
    if (!res.ok) {
      return 'Failed to fetch project list during reconnect sync';
    }
    const { projects } = await res.json();

    for (const project of projects ?? []) {
      if (!project.project_id) continue;

      // Dispatch a project_status event so dashboards refresh
      const statusEvent: WSEvent = {
        type: 'project_status',
        project_id: project.project_id,
        project_name: project.project_name,
        status: project.status ?? 'idle',
        timestamp: Date.now() / 1000,
      };
      for (const cb of subscribers) {
        try { cb(statusEvent); } catch { /* subscriber error */ }
      }

      // For running projects, fetch detailed live state
      if (project.is_running) {
        try {
          const liveRes = await fetch(`/api/projects/${project.project_id}/live`);
          if (liveRes.ok) {
            const liveData = await liveRes.json();
            const liveEvent: WSEvent = {
              type: 'live_state_sync',
              project_id: project.project_id,
              ...liveData,
              timestamp: Date.now() / 1000,
            };
            for (const cb of subscribers) {
              try { cb(liveEvent); } catch { /* subscriber error */ }
            }
          }
        } catch {
          // Ignore per-project fetch errors
        }
      }

      // Request event replay using replay_range for precise gap filling
      const lastSeq = _projectSequences[project.project_id] ?? 0;
      if (lastSeq > 0 && ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({
            type: 'replay_range',
            project_id: project.project_id,
            from_sequence: lastSeq,
            to_sequence: lastSeq + 1000, // server caps at 1000 events
          }));
        } catch {
          replayError = `Failed to request event replay for project "${project.project_name || project.project_id}"`;
        }
      }
    }
  } catch (err) {
    replayError = 'Network error during reconnect sync — some events may be missing';
    console.error('[WS] Sync on reconnect failed:', err);
  }

  return replayError;
}

// ── Exponential backoff with jitter ────────────────────────────────

function computeBackoffDelay(baseMs: number, attempt: number, maxMs: number): number {
  // Exponential: base * 2^attempt, capped at max
  const exponential = Math.min(baseMs * Math.pow(2, attempt), maxMs);
  // Full jitter: uniform random in [0, exponential]
  // This provides better spread than the 50-100% jitter previously used
  return Math.random() * exponential;
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [connectionQuality, setConnectionQuality] = useState<ConnectionQuality>('disconnected');
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [replayError, setReplayError] = useState<string | null>(null);
  const subscribersRef = useRef<Set<Subscriber>>(new Set());
  const wsConfig = getWsConfig();
  const attemptCountRef = useRef(0);
  const mountedRef = useRef(true);
  const wasConnectedRef = useRef(false);
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const heartbeatCheckRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Timestamp of last pong received from server (or last successful message) */
  const lastPongRef = useRef<number>(Date.now());

  // Outbound message queue — buffers messages while disconnected
  const messageQueueRef = useRef<QueuedMessage[]>([]);

  /** Stop all heartbeat timers */
  const stopHeartbeat = useCallback((): void => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (heartbeatCheckRef.current) {
      clearInterval(heartbeatCheckRef.current);
      heartbeatCheckRef.current = null;
    }
  }, []);

  /** Start heartbeat ping/pong mechanism */
  const startHeartbeat = useCallback((ws: WebSocket): void => {
    stopHeartbeat();
    lastPongRef.current = Date.now();

    // Send ping every HEARTBEAT_INTERVAL_MS
    heartbeatIntervalRef.current = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          // Send failed — connection is dead, will be caught by onclose
        }
      }
    }, HEARTBEAT_INTERVAL_MS);

    // Check heartbeat health every 5 seconds
    heartbeatCheckRef.current = setInterval(() => {
      const elapsed = Date.now() - lastPongRef.current;

      if (elapsed >= HEARTBEAT_STALE_THRESHOLD_MS) {
        // Connection is stale — force close to trigger reconnect
        console.warn(`[WS] Heartbeat stale (${Math.round(elapsed / 1000)}s since last pong), forcing reconnect`);
        setConnectionQuality('disconnected');
        ws.close();
      } else if (elapsed >= HEARTBEAT_DEGRADED_THRESHOLD_MS) {
        setConnectionQuality('degraded');
      } else {
        setConnectionQuality('connected');
      }
    }, 5_000);
  }, [stopHeartbeat]);

  /** Flush buffered messages after reconnect + auth, with priority ordering */
  const flushMessageQueue = useCallback((ws: WebSocket) => {
    const now = Date.now();
    const queue = messageQueueRef.current;
    messageQueueRef.current = [];

    // Sort: critical messages first, then by timestamp (oldest first)
    const sorted = queue
      .filter(msg => now - msg.timestamp <= MESSAGE_MAX_AGE_MS)
      .sort((a, b) => {
        if (a.priority !== b.priority) {
          return a.priority === 'critical' ? -1 : 1;
        }
        return a.timestamp - b.timestamp;
      });

    for (const msg of sorted) {
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(msg.payload);
        } catch {
          // Connection is likely dead, onclose will trigger reconnect
        }
      }
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    // Clear any pending reconnect timer
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // Stop heartbeat for any previous connection
    stopHeartbeat();

    // Don't create a new connection if one is already open/connecting
    const existing = wsRef.current;
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws`);

    ws.onopen = () => {
      setConnected(true);
      setAuthenticated(false); // not yet authenticated — waiting for auth_ok
      setConnectionQuality('disconnected'); // still disconnected until auth_ok
      wasConnectedRef.current = true;
      attemptCountRef.current = 0; // reset backoff counter on success
      setReconnectAttempts(0);

      // ── First-frame authentication (SEC-WS) ──
      const token = getAuthToken();
      try {
        ws.send(JSON.stringify({ type: 'auth', device_token: token }));
      } catch {
        // Send failed — will be caught by onclose
      }
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);

        // Handle pong — update heartbeat timestamp, don't dispatch to subscribers
        if (data.type === 'pong') {
          lastPongRef.current = Date.now();
          return;
        }

        // Handle ping — respond with pong and update heartbeat timestamp
        if (data.type === 'ping') {
          lastPongRef.current = Date.now();
          try {
            ws.send(JSON.stringify({ type: 'pong' }));
          } catch {
            // Send failed
          }
          return;
        }

        // ── First-frame auth responses ──
        if (data.type === 'auth_ok') {
          setAuthenticated(true);
          setConnectionQuality('connected');

          // Start heartbeat after successful authentication
          startHeartbeat(ws);

          // Now that we're authenticated, sync state and flush queued messages
          _syncStateOnReconnect(subscribersRef.current, ws).then((error) => {
            if (error) {
              setReplayError(error);
            }
            // Flush outbound queue after sync (priority-ordered)
            flushMessageQueue(ws);
          });
          return;
        }
        if (data.type === 'auth_failed') {
          setAuthenticated(false);
          setConnectionQuality('disconnected');
          // Stop reconnecting — redirect to login
          mountedRef.current = false;
          ws.close();
          window.dispatchEvent(new CustomEvent('hivemind-auth-expired'));
          return;
        }

        // Handle replay batch — dispatch each replayed event to subscribers
        if (data.type === 'replay_batch' || data.type === 'replay_range_batch') {
          const events = data.events ?? [];
          for (const evt of events) {
            if (!evt || typeof evt !== 'object' || !evt.type) continue;
            const event = evt as WSEvent;
            _trackSequence(event);
            for (const cb of subscribersRef.current) {
              try { cb(event); } catch { /* subscriber error */ }
            }
          }
          // Update heartbeat on successful message receipt
          lastPongRef.current = Date.now();
          return;
        }

        // Handle replay errors from server
        if (data.type === 'replay_error') {
          const msg = data.message || data.error || 'Event replay failed';
          setReplayError(`Replay failed: ${msg}`);
          console.warn('[WS] Replay error from server:', msg);
          return;
        }

        // Validate minimum required fields before dispatching
        if (!data.type || typeof data !== 'object') return;

        // Any valid message received counts as a heartbeat signal
        lastPongRef.current = Date.now();

        const event = data as WSEvent;
        _trackSequence(event);
        for (const cb of subscribersRef.current) {
          try {
            cb(event);
          } catch {
            // subscriber error — don't break others
          }
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setAuthenticated(false);
      setConnectionQuality('disconnected');
      // Stop heartbeat for this dead connection
      stopHeartbeat();
      if (!mountedRef.current) return;

      // Exponential backoff with full jitter: prevents thundering herd (STAB-02)
      const attempt = attemptCountRef.current;
      const delay = computeBackoffDelay(
        wsConfig.reconnect_base_delay_ms,
        attempt,
        wsConfig.reconnect_max_delay_ms,
      );
      attemptCountRef.current = attempt + 1;
      setReconnectAttempts(attempt + 1);

      reconnectTimerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [flushMessageQueue, startHeartbeat, stopHeartbeat, wsConfig.reconnect_base_delay_ms, wsConfig.reconnect_max_delay_ms]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    // ── Visibility change handler (critical for iOS Safari) ──
    const handleVisibilityChange = (): void => {
      if (document.visibilityState === 'visible' && mountedRef.current) {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          // Connection is dead — reconnect immediately (reset backoff)
          attemptCountRef.current = 0;
          setReconnectAttempts(0);
          setConnectionQuality('disconnected');
          connect();
        } else if (ws.readyState === WebSocket.OPEN) {
          // iOS Safari often silently kills WS connections when backgrounded.
          // Check connection quality by verifying heartbeat freshness.
          const elapsed = Date.now() - lastPongRef.current;

          if (elapsed >= HEARTBEAT_STALE_THRESHOLD_MS) {
            // Connection is stale — force reconnect
            console.warn('[WS] Stale connection detected on visibility change, reconnecting');
            setConnectionQuality('disconnected');
            ws.close();
          } else {
            // Connection seems alive — send a ping probe and sync state
            if (elapsed >= HEARTBEAT_DEGRADED_THRESHOLD_MS) {
              setConnectionQuality('degraded');
            }
            try {
              ws.send(JSON.stringify({ type: 'ping' }));
            } catch {
              ws.close();
              return;
            }
            _syncStateOnReconnect(subscribersRef.current, ws).then((error) => {
              if (error) setReplayError(error);
            });
          }
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    // ── Page focus handler (backup for visibility change) ──
    const handleFocus = (): void => {
      if (mountedRef.current) {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          attemptCountRef.current = 0;
          setReconnectAttempts(0);
          connect();
        }
      }
    };

    window.addEventListener('focus', handleFocus);

    // ── Online/offline handler ──
    const handleOnline = (): void => {
      if (mountedRef.current) {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          attemptCountRef.current = 0;
          setReconnectAttempts(0);
          connect();
        }
      }
    };

    window.addEventListener('online', handleOnline);

    return () => {
      mountedRef.current = false;
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
      window.removeEventListener('online', handleOnline);
      stopHeartbeat();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close();
    };
  }, [connect, stopHeartbeat]);

  const subscribe = useCallback((callback: Subscriber) => {
    subscribersRef.current.add(callback);
    return () => {
      subscribersRef.current.delete(callback);
    };
  }, []);

  const requestReplay = useCallback((projectId: string, sinceSequence: number) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({
          type: 'replay_range',
          project_id: projectId,
          from_sequence: sinceSequence,
          to_sequence: sinceSequence + 1000,
        }));
      } catch {
        setReplayError(`Failed to request replay for project ${projectId}`);
      }
    }
  }, []);

  /** Send a message through WebSocket, buffering if not connected/authenticated */
  const sendMessage = useCallback((message: Record<string, unknown>) => {
    const payload = JSON.stringify(message);
    const ws = wsRef.current;

    if (ws && ws.readyState === WebSocket.OPEN && authenticated) {
      try {
        ws.send(payload);
        return;
      } catch {
        // Fall through to queue
      }
    }

    // Buffer the message for later delivery with priority classification
    const queue = messageQueueRef.current;
    if (queue.length < MESSAGE_QUEUE_MAX_SIZE) {
      queue.push({
        payload,
        timestamp: Date.now(),
        priority: classifyMessagePriority(payload),
      });
    } else {
      console.warn('[WS] Outbound message queue full, dropping message');
    }
  }, [authenticated]);

  const dismissReplayError = useCallback(() => {
    setReplayError(null);
  }, []);

  return (
    <WSContext.Provider value={{
      connected,
      authenticated,
      connectionQuality,
      subscribe,
      requestReplay,
      sendMessage,
      reconnectAttempts,
      replayError,
      dismissReplayError,
    }}>
      {children}
    </WSContext.Provider>
  );
}

/**
 * Subscribe to WebSocket events. The callback is called for every event.
 * Returns { connected, authenticated, connectionQuality, requestReplay, sendMessage, reconnectAttempts, replayError } for sync control.
 */
export function useWSSubscribe(callback: Subscriber): {
  connected: boolean;
  authenticated: boolean;
  connectionQuality: ConnectionQuality;
  requestReplay: (projectId: string, sinceSequence: number) => void;
  sendMessage: (message: Record<string, unknown>) => void;
  reconnectAttempts: number;
  replayError: string | null;
  dismissReplayError: () => void;
} {
  const { connected, authenticated, connectionQuality, subscribe, requestReplay, sendMessage, reconnectAttempts, replayError, dismissReplayError } = useContext(WSContext);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    return subscribe((event) => callbackRef.current(event));
  }, [subscribe]);

  return { connected, authenticated, connectionQuality, requestReplay, sendMessage, reconnectAttempts, replayError, dismissReplayError };
}

/** Hook to access WebSocket connection status without subscribing to events */
export function useWSStatus(): {
  connected: boolean;
  authenticated: boolean;
  connectionQuality: ConnectionQuality;
  reconnectAttempts: number;
  replayError: string | null;
  dismissReplayError: () => void;
} {
  const { connected, authenticated, connectionQuality, reconnectAttempts, replayError, dismissReplayError } = useContext(WSContext);
  return { connected, authenticated, connectionQuality, reconnectAttempts, replayError, dismissReplayError };
}
