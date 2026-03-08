import { createContext, useContext, useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import type { WSEvent } from './types';

type Subscriber = (event: WSEvent) => void;

interface WSContextValue {
  connected: boolean;
  subscribe: (callback: Subscriber) => () => void;
  /** Request replay of missed events for a project since a given sequence */
  requestReplay: (projectId: string, sinceSequence: number) => void;
}

const WSContext = createContext<WSContextValue>({
  connected: false,
  subscribe: () => () => {},
  requestReplay: () => {},
});

/**
 * Per-project sequence tracker.
 * Tracks the latest sequence_id seen for each project so we can request
 * only missed events on reconnect.
 */
const _projectSequences: Record<string, number> = {};

function _trackSequence(event: WSEvent) {
  if (event.project_id && typeof (event as Record<string, unknown>).sequence_id === 'number') {
    const seq = (event as Record<string, unknown>).sequence_id as number;
    const current = _projectSequences[event.project_id] ?? 0;
    if (seq > current) {
      _projectSequences[event.project_id] = seq;
    }
  }
}

/**
 * After a WebSocket reconnect, fetch live state for all active projects
 * and request event replay for any missed events.
 */
async function _syncStateOnReconnect(
  subscribers: Set<Subscriber>,
  ws: WebSocket | null,
) {
  try {
    const res = await fetch('/api/projects');
    if (!res.ok) return;
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

      // Request event replay for missed events via WebSocket
      const lastSeq = _projectSequences[project.project_id] ?? 0;
      if (lastSeq > 0 && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'replay',
          project_id: project.project_id,
          since_sequence: lastSeq,
        }));
      }
    }
  } catch {
    // Network error during sync — will retry on next reconnect
  }
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const subscribersRef = useRef<Set<Subscriber>>(new Set());
  const retryDelayRef = useRef(1000);
  const mountedRef = useRef(true);
  const wasConnectedRef = useRef(false);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws`);

    ws.onopen = () => {
      const isReconnect = wasConnectedRef.current;
      setConnected(true);
      wasConnectedRef.current = true;
      retryDelayRef.current = 1000; // reset backoff on success

      // On reconnect, sync state so UI catches up on missed events
      if (isReconnect) {
        _syncStateOnReconnect(subscribersRef.current, ws);
      }
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        // Handle ping/pong at transport level — don't dispatch to subscribers
        if (data.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
          return;
        }

        // Handle replay batch — dispatch each replayed event to subscribers
        if (data.type === 'replay_batch') {
          const events = data.events ?? [];
          for (const evt of events) {
            const event = evt as WSEvent;
            _trackSequence(event);
            for (const cb of subscribersRef.current) {
              try { cb(event); } catch { /* subscriber error */ }
            }
          }
          return;
        }

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
      if (!mountedRef.current) return;
      // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 2, 30000);
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
    };
  }, [connect]);

  const subscribe = useCallback((callback: Subscriber) => {
    subscribersRef.current.add(callback);
    return () => {
      subscribersRef.current.delete(callback);
    };
  }, []);

  const requestReplay = useCallback((projectId: string, sinceSequence: number) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'replay',
        project_id: projectId,
        since_sequence: sinceSequence,
      }));
    }
  }, []);

  return (
    <WSContext.Provider value={{ connected, subscribe, requestReplay }}>
      {children}
    </WSContext.Provider>
  );
}

/**
 * Subscribe to WebSocket events. The callback is called for every event.
 * Returns { connected, requestReplay } for sync control.
 */
export function useWSSubscribe(callback: Subscriber): {
  connected: boolean;
  requestReplay: (projectId: string, sinceSequence: number) => void;
} {
  const { connected, subscribe, requestReplay } = useContext(WSContext);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    return subscribe((event) => callbackRef.current(event));
  }, [subscribe]);

  return { connected, requestReplay };
}
